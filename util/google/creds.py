import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from util.logging.logger import Logging

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/drive"
]
DEFAULT_CRED_LOCATION = "./creds/"

def get_creds(credential_location):
  creds = None
  service = None
  
  logger = Logging.get("drive-cred")

  # The file token.json stores the user's access and refresh tokens, and is
  # created automatically when the authorization flow completes for the first
  # time.
  credential_location = credential_location or DEFAULT_CRED_LOCATION
  credential_file = f"{credential_location}/creds.json"
  token_file = f"{credential_location}/token.json"


  if os.path.exists(token_file):
    creds = Credentials.from_authorized_user_file(token_file, SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          credential_file, SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open(token_file, "w") as token:
      token.write(creds.to_json())

  try:
    service = build("drive", "v3", credentials=creds)

  except HttpError as error:
    # TODO(developer) - Handle errors from drive API.
    logger.error(f"An error occurred authenticating: {error}")
  
  return service