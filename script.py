from project_context.api_drive import GoogleDriveManager
from project_context.browser.browser import Browser
from project_context.utils import generate_context

cookies_path = r"aistudio.google.com_cookies.txt"
browser = Browser(cookies_path=cookies_path)
models = browser.chat.get_models()
print(browser.chat.model_current)


project_path = r"D:\github Leo\servercontrol"
content = generate_context(project_path)

api = GoogleDriveManager()
files = api.list_files_google_ia_studio()
print(files)
