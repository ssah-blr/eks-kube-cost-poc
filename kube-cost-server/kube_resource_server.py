import boto3
import json
import os
import logging
from flask import Flask, request, jsonify
from botocore.exceptions import BotoCoreError, ClientError

# Flask app
app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Region mapping for AWS
REGION_MAPPING = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-central-1": "EU (Frankfurt)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "sa-east-1": "South America (Sao Paulo)",
    "ca-central-1": "Canada (Central)",
    "us-gov-west-1": "AWS GovCloud (US-West)",
    "us-gov-east-1": "AWS GovCloud (US-East)",
    "cn-north-1": "China (Beijing)",
    "cn-northwest-1": "China (Ningxia)",
    "eu-north-1": "EU (Stockholm)",
    "me-south-1": "Middle East (Bahrain)",
    "af-south-1": "Africa (Cape Town)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
    "ap-south-2": "Asia Pacific (Hyderabad)",
    "eu-south-1": "EU (Milan)",
    "eu-south-2": "EU (Madrid)",
}


def get_pricing_data(region, instance_type, operating_system):
    """Fetch pricing data and calculate cost per vCPU for the given parameters."""
    try:
        # Convert short region name to full name
        full_region_name = REGION_MAPPING.get(region)
        if not full_region_name:
            raise ValueError(f"Invalid region: {region}")

        session = boto3.Session()

        # Use the Pricing API client
        pricing_client = session.client("pricing", region_name="us-east-1")

        # Construct the filters for the pricing query
        filters = [
            {"Type": "TERM_MATCH", "Field": "location", "Value": full_region_name},
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": operating_system},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            {"Type": "TERM_MATCH", "Field": "locationType", "Value": "AWS Region"},
            {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Compute Instance"},
            {"Type": "TERM_MATCH", "Field": "operation", "Value": "RunInstances"},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        ]

        logger.info("Filters used for pricing query: %s", filters)

        # Query the AWS Pricing API
        response = pricing_client.get_products(
            ServiceCode="AmazonEC2",
            Filters=filters,
            FormatVersion="aws_v1"
        )

        if not response['PriceList']:
            raise ValueError("No pricing data found for the given parameters")

        # Extract pricing and vCPU details
        price_list = json.loads(response['PriceList'][0])
        attributes = price_list['product']['attributes']
        terms = price_list['terms']['OnDemand']

        # Extract vCPU count
        vcpu_count = attributes.get('vcpu', 'Unknown')
        if vcpu_count == 'Unknown':
            raise ValueError(f"Unable to determine vCPU count for instance type {instance_type}")

        vcpu_count = int(vcpu_count)  # Convert to integer

        # Extract pricing details
        for term_id, term_data in terms.items():
            for dimension_id, dimension_data in term_data['priceDimensions'].items():
                price_per_unit = float(dimension_data['pricePerUnit']['USD'])
                per_cpu_cost = price_per_unit / vcpu_count
                return round(price_per_unit, 4), round(per_cpu_cost, 4), vcpu_count

    except (BotoCoreError, ClientError) as e:
        logger.error("AWS API error: %s", e)
    except Exception as e:
        logger.error("Error occurred: %s", e)
    return None, None, None


@app.route('/api/pricing', methods=['POST'])
def pricing_api():
    """API endpoint to fetch instance pricing data."""
    data = request.get_json()
    print("Received data:", data)  # Debugging line
    if not data:
        return jsonify({"error": "No data received"}), 400
    
    region = data.get('region')
    instance_type = data.get('instance_type')
    operating_system = data.get('operating_system')

    print(f"{region} {instance_type} {operating_system}")

    if not all([region, instance_type, operating_system]):
        return jsonify({"error": "Missing required parameters: region, instance_type, operating_system"}), 400

    price, per_cpu_cost, vcpu_count = get_pricing_data(region, instance_type, operating_system)
    if price is not None:
        return jsonify({
            "instance_cost_per_hour": price,
            "cost_per_vcpu_per_hour": per_cpu_cost,
            "vcpu_count": vcpu_count
        })
    else:
        return jsonify({"error": "Could not fetch pricing data"}), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)
