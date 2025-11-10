from project_context.browser.browser import Browser

cookies_path = r"aistudio.google.com_cookies.txt"
browser = Browser(cookies_path=cookies_path)
models = browser.chat.get_models()
response, chat_id = browser.chat.write_prompt("Hola", thinking_mode=False)

print(response)
# project_path = r"D:\github Leo\servercontrol"
# content = generate_context(project_path)

# api = GoogleDriveManager()
# files = api.list_files_google_ia_studio()
# print(files)
