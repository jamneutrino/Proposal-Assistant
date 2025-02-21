from google.oauth2 import service_account
from googleapiclient.discovery import build
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
SPREADSHEET_ID = os.getenv('GOOGLE_SPREADSHEET_ID', '1VKlVOnVBuNrN4Kb9QaBAP3fGmxZgKdgnu3izx6MkP_k')
RANGE_NAME = 'Sheet1!A:B'  # Adjust if your sheet name is different

def get_sheet_data():
    """
    Reads the price data from Google Sheets and returns a dictionary of item names and prices
    """
    try:
        # Create credentials dict from environment variables
        credentials_dict = {
            "type": "service_account",
            "project_id": os.getenv('GOOGLE_PROJECT_ID'),
            "private_key_id": os.getenv('GOOGLE_PRIVATE_KEY_ID'),
            "private_key": os.getenv('GOOGLE_PRIVATE_KEY'),
            "client_email": os.getenv('GOOGLE_CLIENT_EMAIL'),
            "client_id": os.getenv('GOOGLE_CLIENT_ID'),
            "auth_uri": os.getenv('GOOGLE_AUTH_URI'),
            "token_uri": os.getenv('GOOGLE_TOKEN_URI'),
            "auth_provider_x509_cert_url": os.getenv('GOOGLE_AUTH_PROVIDER_CERT_URL'),
            "client_x509_cert_url": os.getenv('GOOGLE_CLIENT_CERT_URL')
        }

        # Create credentials from the dictionary
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict, scopes=SCOPES)

        # Build the service
        service = build('sheets', 'v4', credentials=creds)

        # Call the Sheets API
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID,
                                  range=RANGE_NAME).execute()
        values = result.get('values', [])

        if not values:
            print('No data found.')
            return {}

        # Convert to dictionary, skipping header row
        prices = {}
        for row in values[1:]:  # Skip header row
            if len(row) >= 2:  # Ensure row has both item name and price
                try:
                    # Remove any whitespace and convert price to float
                    item_name = row[0].strip()
                    price = float(row[1].strip().replace('$', '').replace(',', ''))
                    prices[item_name] = price
                except (ValueError, IndexError) as e:
                    print(f"Error processing row {row}: {e}")
                    continue

        return prices

    except Exception as e:
        print(f"Error accessing Google Sheets: {e}")
        return {} 