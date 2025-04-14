import boto3 #AWS SDK for Python (used to interact with EC2 and Auto Scaling).
import logging # For writing logs to CloudWatch.

# Logging setup
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS Clients
ec2 = boto3.client('ec2')
autoscaling = boto3.client('autoscaling')

# Key Values
asg_name = "Auto Scaling Name"                   # Auto Scaling Group name
launch_template_name = "Lauch Template Name"  # Launch Template name

from dateutil.parser import parse as parse_date #For parsing the AMI creation dates.

def get_latest_ami():
    instance_id = 'i-xxxxxxxx'  # Set your instance ID here (Key Value)

    # Filters the AMI's that contain the instance_id and are avaible
    response = ec2.describe_images(
        Filters=[
            {'Name': 'name', 'Values': [f'*{instance_id}*']},
            {'Name': 'state', 'Values': ['available']}
        ],
        Owners=['self']
    )

    images = response['Images']
    logger.info(f"Total filtered AMIs found: {len(images)}")
    for img in images:
        logger.info(f"Found AMI: ID={img['ImageId']}, Name={img['Name']}, Created={img['CreationDate']}")

    #Sorts all matching AMIs by creation date, most recent first.
    sorted_images = sorted(images, key=lambda x: parse_date(x['CreationDate']), reverse=True)
    latest_image = sorted_images[0] if sorted_images else None

    #Returns the most recent AMI ID or raises an error if none are found.
    if latest_image:
        logger.info(f"Selected latest AMI: {latest_image['ImageId']} (Created: {latest_image['CreationDate']})")
        return latest_image['ImageId']
    else:
        raise Exception("No matching AMIs found.")


#Retrieves the Launch Template ID using its name. Required to create a new version later.
def get_launch_template_id():
    response = ec2.describe_launch_templates(LaunchTemplateNames=[launch_template_name])
    return response['LaunchTemplates'][0]['LaunchTemplateId']

# (1)Fetches the latest version of the launch template.(2)Extracts the configuration and replaces the AMI ID with the newly found one.
def create_new_template_version(launch_template_id, ami_id):
    # Get the latest launch template version
    existing = ec2.describe_launch_template_versions(
        LaunchTemplateId=launch_template_id,
        Versions=['$Latest']
    )
    old_data = existing['LaunchTemplateVersions'][0]['LaunchTemplateData']

    # Preserve the existing instance type and other settings
    instance_type = old_data.get('InstanceType', 't2.large')  # Default to 't2.large' if not set

    # Replace only the AMI while keeping the rest (instance type, security groups, etc.)
    old_data['ImageId'] = ami_id  # Ensure we're using the correct AMI ID

    # Log the changes to verify the AMI ID
    logger.info(f"Updating launch template version with AMI ID: {ami_id}")

    # Creates a new version of the template with the new AMI.
    response = ec2.create_launch_template_version(
        LaunchTemplateId=launch_template_id,
        VersionDescription=f"Updated to AMI {ami_id}",
        LaunchTemplateData=old_data
    )

    version_number = response['LaunchTemplateVersion']['VersionNumber']
    logger.info(f"Created new launch template version: {version_number}")
    return version_number


#Updates the Auto Scaling Group to use the newest version of the launch template.
def update_asg_launch_template(version_number):
    logger.info(f"Updating ASG with launch template version: {version_number}")
    
    autoscaling.update_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        LaunchTemplate={
            'LaunchTemplateName': launch_template_name,
            'Version': str(version_number)  # Ensure the new version is being passed here
        }
    )
    logger.info(f"ASG updated to launch template version: {version_number}")

# (1)Triggers a rolling instance refresh, replacing old EC2 instances gradually.(2) MinHealthyPercentage: 100 ensures zero downtime during the rollout.
def trigger_instance_refresh():
    response = autoscaling.start_instance_refresh(
        AutoScalingGroupName=asg_name,
        Strategy='Rolling',
        Preferences={
            'MinHealthyPercentage': 100,
            'InstanceWarmup': 300
        }
    )
    logger.info(f"Instance refresh started: {response['InstanceRefreshId']}")

#The entry point of the Lambda function.
# (1)Finds the latest AMI.
#(2) Gets the launch template ID.
#(3) Creates a new version.
#(4) Updates the ASG.
#(5) Triggers the refresh.


def lambda_handler(event, context):
    try:
        ami_id = get_latest_ami()
        lt_id = get_launch_template_id()
        new_version = create_new_template_version(lt_id, ami_id)
        update_asg_launch_template(new_version)
        trigger_instance_refresh()
        
        return {
            'statusCode': 200,
            'body': f'AMI updated to {ami_id} and instance refresh started.'
        }
    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': f'Error: {str(e)}'
        }

