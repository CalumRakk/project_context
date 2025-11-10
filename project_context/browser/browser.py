import logging
from pathlib import Path
from typing import Union

import undetected_chromedriver as us
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from project_context.browser.pages.chat import ChatBrowser

from ..parser import SeleniumCookie, load_netscape_cookies

logger = logging.getLogger(__name__)


class Browser:
    def __init__(self, cookies_path: Union[str, Path], headless=False):
        self.headless = headless
        self.driver = self._start()

        self.chat = ChatBrowser(self.driver)

        # login y check
        cookies = load_netscape_cookies(cookies_path)
        self.set_cookies(cookies)
        if not self.is_login():
            raise Exception("No se pudo iniciar sesion")

    def is_login(self):
        try:
            xpath = f"//div[contains(@class, 'account-switcher-container')]"
            wait = WebDriverWait(self.driver, 5)
            wait.until(EC.presence_of_element_located(("xpath", xpath)))
            return True
        except:
            return False

    def _start(self) -> us.Chrome:
        """Inicializa el driver si no existe todavía."""

        logger.info("Iniciando driver...")
        options = us.ChromeOptions()

        if self.headless:
            options.add_argument("--headless=new")

        options.add_argument("--disable-extensions")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--disable-infobars")
        options.add_argument("--no-first-run")
        options.add_argument("--disable-session-crashed-bubble")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-notifications")
        options.add_argument("--no-sandbox")
        options.add_argument("--lang=en-US")

        service = ChromeService(executable_path=ChromeDriverManager().install())
        self._driver = us.Chrome(
            service=service, options=options, headless=self.headless
        )
        self._driver.set_window_size(1000, 600)
        logger.info("Driver iniciado.")
        return self._driver

    # def refresh(self):
    #     """Refresca la página actual y espera explícitamente 5 segundos."""
    #     self.driver.refresh()
    #     self.driver.implicitly_wait(5)

    # def get(self, url):
    #     self.driver.get(url)

    def close(self):
        self.driver.quit()

    def set_cookies(self, cookies: list[SeleniumCookie]):
        logger.info("Agregando cookies...")

        cookies_by_domain: dict[str, list[SeleniumCookie]] = {}
        for cookie in cookies:
            cookies_dict = cookie.model_dump()
            domain_url = cookies_dict["domain"]
            url = f"https://{domain_url.strip('.')}/"
            if url not in cookies_by_domain:
                cookies_by_domain[url] = []

            cookies_by_domain[url].append(cookie)

            # current_url = self.driver.current_url
            # if domain_url not in current_url:
            #     self.driver.get(f"https://{domain_url.strip('.')}/")
            #     self.driver.implicitly_wait(5)

        for domain_url, cookies_list in cookies_by_domain.items():
            self.driver.get(domain_url)
            for cookie in cookies_list:
                cookies_dict = cookie.model_dump()
                if cookies_dict["expiry"] == 0:
                    cookies_dict.pop("expiry")
                self.driver.add_cookie(cookies_dict)

            self.driver.refresh()

        self.driver.get("https://aistudio.google.com/")
        self.accept_dialog_autosave()
        logger.info("Cookies agregadas.")

    def accept_dialog_autosave(self):
        logger.info("Aceptando dialogo de autosave...")
        wait = WebDriverWait(self.driver, 10)
        dialog = wait.until(
            EC.presence_of_element_located(("xpath", "//div[@class='dialog']"))
        )
        dialog.find_element("xpath", ".//button").click()

    def simulate_escape(self):
        logger.info("Simulando escape...")
        body_box = self.driver.find_element("tag name", "body")
        body_box.send_keys(Keys.ESCAPE)

    # def _wait_chat_history_loaded(self):
    #     role_value = "progressbar"
    #     wait = WebDriverWait(self.driver, 10)
    #     wait.until(
    #         EC.invisibility_of_element_located(("xpath", f".//*[@role='{role_value}']"))
    #     )

    # def get_chats(self):
    #     url = "https://aistudio.google.com/app/library"
    #     self.driver.get(url)

    #     self._wait_chat_history_loaded()

    #     wait = WebDriverWait(self.driver, 5)
    #     rowgroups = wait.until(
    #         EC.presence_of_element_located(("xpath", ".//tbody[@role='rowgroup']"))
    #     )
    #     chats = []
    #     for row in rowgroups.find_elements("xpath", ".//tr[@role='row']"):
    #         second_element_td = row.find_elements("xpath", ".//td")[1]
    #         a_element = second_element_td.find_element("xpath", ".//a")
    #         chat_title = a_element.text
    #         chat_link = a_element.get_attribute("href")
    #         chats.append({"title": chat_title, "link": chat_link})
    #     return chats

    # def go_to_chat(self):
    #     if "prompts" in self.driver.current_url:
    #         return
    #     url = "https://aistudio.google.com/app/prompts/new_chat"
    #     self.driver.get(url)

    #     wait = WebDriverWait(self.driver, 5)
    #     wait.until(
    #         EC.presence_of_element_located(("xpath", "//button[@aria-label='Run']"))
    #     )

    # def _select_model_current(self):
    #     if not "prompts" in self.driver.current_url:
    #         self.go_to_chat()
    #     wait = WebDriverWait(self.driver, 5)
    #     element = wait.until(
    #         EC.presence_of_element_located(
    #             ("xpath", "//span[@data-test-id='model-name']")
    #         )
    #     )
    #     element.click()

    # def _open_model_selection_and_click_all(self):
    #     self._select_model_current()

    #     wait = WebDriverWait(self.driver, 5)
    #     model_carousel_selector = wait.until(
    #         EC.presence_of_element_located(
    #             (
    #                 "xpath",
    #                 "//ms-model-carousel[@data-test-id='model-carousel-in-selector']",
    #             )
    #         )
    #     )
    #     model_carousel_selector.find_element(
    #         "xpath", ".//button[@variant='filter-chip' and contains(text(), 'All')]"
    #     ).click()

    # def get_models(self):
    #     """Devuelve los nombre de los modelos disponibles."""
    #     if not "prompts" in self.driver.current_url:
    #         self.go_to_chat()

    #     self._open_model_selection_and_click_all()

    #     wait = WebDriverWait(self.driver, 5)
    #     model_container = wait.until(
    #         EC.presence_of_element_located(
    #             (
    #                 "xpath",
    #                 "//ms-model-carousel[@data-test-id='model-carousel-in-selector']",
    #             )
    #         )
    #     )
    #     models = []
    #     for model in model_container.find_elements("xpath", ".//ms-model-carousel-row"):
    #         element_title = model.find_element(
    #             "xpath", ".//span[@class='model-title-text ellipses']"
    #         )
    #         models.append(element_title.text)
    #     return models

    # def select_model(self, model_name: str):
    #     models = self.get_models()
    #     if model_name not in models:
    #         raise Exception(f"Modelo {model_name} no encontrado")

    #     model_carousel_selector = self.driver.find_element(
    #         "xpath", "//ms-model-carousel[@data-test-id='model-carousel-in-selector']"
    #     )
    #     model_carousel_selector.find_element(
    #         "xpath", f".//ms-model-carousel-row//span[text()='{model_name}']"
    #     ).click()
