from project_context.api_drive import GoogleDriveManager

api = GoogleDriveManager()

folder_root = "root"
files = api.list_files_google_ia_studio()
print(files)
