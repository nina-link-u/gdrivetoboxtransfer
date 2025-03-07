import re
import os
import json
import time
from io import BytesIO
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from boxsdk import OAuth2,Client
from io import BytesIO
from googleapiclient.http import MediaIoBaseDownload

# Google Drive setup using token.json

SCOPES = ['https://www.googleapis.com/auth/drive']
creds = None
if os.path.exists('token.json'):
    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        raise ValueError("No valid credentials available. Run initial authorization flow.")
drive_service = build('drive', 'v3', credentials=creds)

BOX_TOKEN_FILE = 'box_token.json'

def load_tokens():
    """
    Load the client credentials and tokens from a JSON file.
    """
    if not os.path.exists(BOX_TOKEN_FILE):
        raise Exception("Token file not found. Please perform the initial OAuth 2.0 authorization to generate tokens.")
    with open(BOX_TOKEN_FILE, 'r') as f:
        data = json.load(f)

    if not data.get("refresh_token"):
        raise Exception("No refresh token found. It looks like you're using a developer token. "
                        "Please perform the manual OAuth 2.0 flow once to obtain a proper refresh token.")
    return (
        data.get("client_id"),
        data.get("client_secret"),
        data.get("access_token"),
        data.get("refresh_token")
    )

def store_tokens(access_token, refresh_token):
    """
    Updates the JSON file with the new tokens while preserving the client credentials.
    """
    client_id, client_secret, _, _ = load_tokens()
    new_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": access_token,
        "refresh_token": refresh_token
    }
    with open(BOX_TOKEN_FILE, 'w') as f:
        json.dump(new_data, f)
    print("Tokens have been updated and stored.")

def authenticate_box():
    """
    Box authentication function. 
    """
    client_id, client_secret, access_token, refresh_token = load_tokens()
    oauth2 = OAuth2(
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        refresh_token=refresh_token,
        store_tokens=store_tokens,
    )
    return Client(oauth2)
client = authenticate_box()

def get_root_folder_id(shared_folder_link: str) -> str:
    """
    Extract the Google Drive folder ID from the provided shared folder link.

    """
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', shared_folder_link)
    if match:
        return match.group(1)
    match = re.search(r'open\?id=([a-zA-Z0-9_-]+)', shared_folder_link)
    if match:
        return match.group(1)
    raise ValueError("Invalid Google Drive shared folder link format.")

def find_lettering_folder(root_folder_id: str) -> dict:
    """
    Find all folders inside the provided Google Drive root folder with a 'Lettering' subfolder.
    """
    query = f"'{root_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(
        q=query,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    folders = {}
    for folder in results.get('files', []):
        subquery = f"'{folder['id']}' in parents and name='Lettering' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        subfolders = drive_service.files().list(
            q=subquery,
            corpora='allDrives',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        if subfolders.get('files'):
            folders[folder['name']] = folder['id']
    return folders

def format_folder_name(name: str) -> str:
    """
    Format folder names to 0000 format.
    """
    if re.match(r'^[0-9]+$', name):
        return name.zfill(4)
    return name


def get_box_root_folder(shared_folder_link: str):

    try:
        item = client.get_shared_item(shared_folder_link)
        if item.type == 'folder':
            return item
        else:
            raise ValueError("The shared link does not refer to a folder.")
    except Exception as e:
        raise ValueError("Invalid Box shared folder link format or unable to retrieve folder: " + str(e))


def clean_name(name: str) -> str:
    """
    Removes all spaces and non-alphabetical characters from the name and converts it to lowercase.
    """
    return re.sub(r'[^a-z]', '', name.lower())

def find_title_box_fodler(folder_name: str, box_folders) -> object:
    """
    Compares titles' folders at drive and box and returns matched ones.
    """
    target = clean_name(folder_name)
    for box_folder in box_folders:
        if hasattr(box_folder, 'name'):
            if target in clean_name(box_folder.name):
                return box_folder
    return None



def upload_file_with_retry(box_folder, fh, file_name, max_retries=10, timeout=20):
    """
    Attempts to upload files to box. In case of any problem during the process, tries again after timeout up to 10 times.
    """
    attempt = 0
    while attempt < max_retries:
        try:
            fh.seek(0) 
            box_folder.upload_stream(fh, file_name)
            print(f"Uploaded '{file_name}' to Box folder '{box_folder.name}'.")
            return True
        except Exception as e:
            error_str = str(e)
            if "SSLError" in error_str or "refreshing tokens" in error_str:
                attempt += 1
                print(f"Error uploading '{file_name}': {e}. Retrying in {timeout} seconds... (Attempt {attempt}/{max_retries})")
                time.sleep(timeout)
            else:
                print(f"Error uploading '{file_name}': {e}. Not retrying.")
                return False
    print(f"Failed to upload '{file_name}' after {max_retries} attempts. Skipping.")
    return False


def copy_google_folder_to_box(google_folder_id: str, box_parent_id: str, folder_name: str):
    # Create a new folder in Box with the given folder_name under box_parent_id.
    new_box_folder = client.folder(folder_id=box_parent_id).create_subfolder(folder_name)
    print(f"Created Box folder '{folder_name}' (ID: {new_box_folder.id}) for copying files.")
    
    # Get a list of existing files in the new Box folder to avoid conflicts.
    existing_files = {item.name for item in new_box_folder.get_items(limit=1000) if item.type == 'file'}
    
    # Query for non-folder files in the Google Drive folder.
    query = f"'{google_folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(
        q=query,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get('files', [])
    
    for file in files:
        if file['name'] in existing_files:
            print(f"File '{file['name']}' already exists in Box folder '{folder_name}'. Skipping.")
            continue
        
        print(f"Copying file '{file['name']}' (ID: {file['id']})...")
        request = drive_service.files().get_media(fileId=file['id'])
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Download {int(status.progress() * 100)}% complete for '{file['name']}'")
        fh.seek(0)
        upload_file_with_retry(new_box_folder, fh, file['name'])


def process_numbered_subfolders(google_folder_id: str, box_folder):
    # Locate the "Lettering" subfolder in the Google folder.
    lettering_query = (
        f"'{google_folder_id}' in parents and name='Lettering' and "
        f"mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    lettering_results = drive_service.files().list(
        q=lettering_query,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    lettering_files = lettering_results.get('files', [])
    if not lettering_files:
        print(f"No 'Lettering' subfolder found in Google folder {google_folder_id}.")
        return
    lettering_folder = lettering_files[0]
    print(f"Found 'Lettering' subfolder: {lettering_folder['name']} (ID: {lettering_folder['id']})")

    query = (
        f"'{lettering_folder['id']}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    results = drive_service.files().list(
        q=query,
        corpora='allDrives',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    subfolders = results.get('files', [])
    
    for subfolder in subfolders:
        if re.match(r'^\d+(\.\d+)?$', subfolder['name']):
            formatted_name = format_folder_name(subfolder['name'])
            print(f"\nProcessing Google subfolder: Original '{subfolder['name']}' -> Formatted as '{formatted_name}'")
            try:
                num = int(float(subfolder['name']))
            except ValueError:
                print(f"Skipping '{subfolder['name']}' - cannot parse number.")
                continue
            
            # Determine numeric range.
            range_start = ((num - 1) // 100) * 100 + 1
            range_end = range_start + 99
            range_name = f"{range_start:03d}_{range_end:03d}"
            print(f"Google subfolder '{subfolder['name']}' (formatted as '{formatted_name}') should be in Box numeric range folder '{range_name}'.")
            
            # Find the numeric range folder within the matched Box folder.
            box_range_folder = None
            for item in box_folder.get_items():
                if item.type == 'folder' and (item.name == range_name or item.name == range_name.replace("_", "-")):
                    box_range_folder = item
                    break
            
            # If the range folder is not found, it created using dash format.
            if not box_range_folder:
                print(f"Box numeric range folder '{range_name}' not found for '{formatted_name}'. Creating it...")
                box_range_folder = client.folder(folder_id=box_folder.id).create_subfolder(range_name)
                print(f"Created Box numeric range folder: {box_range_folder.name} (ID: {box_range_folder.id})")
            
            range_items = list(box_range_folder.get_items(limit=1000))
            
            print(f"Contents of Box range folder '{range_name}':")
            for item in range_items:
                if item.type == 'folder':
                    print(f"  - {item.name} (ID: {item.id})")
            
            # Check if a folder with the formatted name already exists in the numeric range folder.
            existing_box_folder = None
            for item in range_items:
                if item.type == 'folder' and item.name == formatted_name:
                    existing_box_folder = item
                    break
            
            if existing_box_folder:
                print(f"Folder '{formatted_name}' already exists in Box range folder '{range_name}'. Checking for missing files...")
                # Query for non-folder files in the Google Drive subfolder.
                query = (
                    f"'{subfolder['id']}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed=false"
                )
                results = drive_service.files().list(
                    q=query,
                    corpora='allDrives',
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()
                google_files = results.get('files', [])
                
                # Get a list of existing files in the Box folder.
                existing_files = {item.name for item in existing_box_folder.get_items(limit=1000) if item.type == 'file'}
                
                for file in google_files:
                    if file['name'] in existing_files:
                        print(f"File '{file['name']}' already exists in Box folder '{formatted_name}'. Skipping.")
                        continue
                    
                    print(f"Copying file '{file['name']}' (ID: {file['id']})...")
                    request = drive_service.files().get_media(fileId=file['id'])
                    fh = BytesIO()
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                        if status:
                            print(f"Download {int(status.progress() * 100)}% complete for '{file['name']}'")
                    fh.seek(0)
                    
                    upload_file_with_retry(existing_box_folder, fh, file['name'])
            else:
                print(f"Folder '{formatted_name}' does not exist in Box range folder '{range_name}'. Copying...")
                copy_google_folder_to_box(subfolder['id'], box_range_folder.id, formatted_name)


def main():
    try:
        
        google_shared_folder_link = 'https://drive.google.com/drive/folders/1SlApgm_Db7c4fHX3sEtY4fUitq06_QLp'  # e.g., "https://drive.google.com/drive/folders/1ABCxyz123"
        google_root_folder_id = get_root_folder_id(google_shared_folder_link)
        
        
        drive_folders = find_lettering_folder(google_root_folder_id)
        if not drive_folders:
            raise ValueError("No folders with a 'Lettering' subfolder were found in the provided Google Drive root folder.")
        
        
        box_shared_folder_link = 'https://shogakukan.box.com/s/lz5r36i84lbgg56bl12feln171dhpel1'
        box_root_folder = get_box_root_folder(box_shared_folder_link)


        
        for folder_name, folder_id in drive_folders.items():
            print(f"\nFound Google Drive folder: {folder_name} with 'Lettering' inside (ID: {folder_id})")
            # Retrieve all folders in the Box shared drive
            box_folders = client.folder(folder_id=box_root_folder.id).get_items(limit=1000)
            box_folder = find_title_box_fodler(folder_name, box_folders)
            if not box_folder:
                print("This folder wasnt found at box", folder_name)
                continue
            print(f"Matched with Box folder: {box_folder.name} (ID: {box_folder.id})")
            
           
            process_numbered_subfolders(folder_id, box_folder)
            
    except HttpError as error:
        print(f"An error occurred: {error}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    main()
