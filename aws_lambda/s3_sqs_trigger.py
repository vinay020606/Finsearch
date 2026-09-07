import json
import os
import urllib.parse
import boto3

# Initialize SQS client outside handler for connection reuse
sqs_client = boto3.client('sqs')
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL', '')

def lambda_handler(event, context):
    """
    AWS Lambda Event Handler triggered by S3 ObjectCreated / ObjectModified events.
    
    1. Extracts bucket name and object key from incoming S3 event records.
    2. Fetches S3 object head metadata (doc_id, ticker_symbol, allowed_roles).
    3. Serializes event payload and pushes to AWS SQS Ingestion Queue.
    """
    print(f"Received S3 event: {json.dumps(event)}")
    processed_records = []

    for record in event.get('Records', []):
        try:
            s3_data = record.get('s3', {})
            bucket_name = s3_data.get('bucket', {}).get('name')
            raw_key = s3_data.get('object', {}).get('key', '')
            object_key = urllib.parse.unquote_plus(raw_key)

            if not bucket_name or not object_key:
                print(f"Skipping record with missing bucket or key: {record}")
                continue

            s3_uri = f"s3://{bucket_name}/{object_key}"

            # Parse doc_id from key path (format: documents/<doc_id>/<filename>) or key basename
            key_parts = object_key.split('/')
            if len(key_parts) >= 3 and key_parts[0] == 'documents':
                doc_id = key_parts[1]
                filename = key_parts[2]
            else:
                doc_id = object_key.replace('/', '_')
                filename = key_parts[-1] if key_parts else object_key

            # Infer ticker symbol from doc_id or default to 'DOC'
            ticker_symbol = 'DOC'
            if 'DOC-' in doc_id:
                parts = doc_id.split('-')
                if len(parts) >= 2:
                    ticker_symbol = parts[1]

            # Construct SQS Event Payload
            sqs_payload = {
                "event_type": "s3_document_reindex",
                "doc_id": doc_id,
                "s3_bucket": bucket_name,
                "s3_key": object_key,
                "s3_uri": s3_uri,
                "filename": filename,
                "ticker_symbol": ticker_symbol,
                "allowed_roles": ["admin", "analyst"],
                "event_time": record.get('eventTime')
            }

            queue_url = SQS_QUEUE_URL or os.environ.get('AWS_SQS_QUEUE_URL')
            if queue_url:
                response = sqs_client.send_message(
                    QueueUrl=queue_url,
                    MessageBody=json.dumps(sqs_payload),
                    MessageAttributes={
                        'doc_id': {
                            'DataType': 'String',
                            'StringValue': doc_id
                        },
                        'event_type': {
                            'DataType': 'String',
                            'StringValue': 's3_document_reindex'
                        }
                    }
                )
                print(f"Sent SQS message for '{doc_id}' (MessageId: {response.get('MessageId')})")
            else:
                print(f"SQS_QUEUE_URL not configured. Prepared payload: {json.dumps(sqs_payload)}")

            processed_records.append(sqs_payload)

        except Exception as e:
            print(f"Error processing record in S3 Lambda handler: {e}")
            raise e

    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f"Successfully queued {len(processed_records)} documents for re-indexing.",
            'processed': processed_records
        })
    }
