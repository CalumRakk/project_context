import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional, Union

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from project_context.browser.utils import BrowserUtils


class ChatBrowser:
    def __init__(self, driver: webdriver.Chrome, debug: bool = False):
        self.driver = driver
        self.utils = BrowserUtils(driver)
        self.debug = debug
        self._screenshot_dir = Path("logs/screens")
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _debug_screenshot(self, name: str):
        """Guarda una captura de pantalla si el modo debug está activado."""
        if not self.debug:
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = self._screenshot_dir / f"{timestamp}_{name}.png"
        try:
            self.driver.save_screenshot(str(filename))
        except Exception as e:
            print(f"[DEBUG] No se pudo guardar screenshot '{name}': {e}")

    @property
    def model_current(self):
        if not hasattr(self, "_model_current"):
            self._model_current = self._get_model_current()
        return self._model_current

    def _get_model_current(self):
        self.open()
        model_name = self.driver.find_element(
            "xpath", "//span[@data-test-id='model-name']"
        ).text.strip()
        self._debug_screenshot("get_model_current")
        return model_name

    def open(self):
        if "prompts" in self.driver.current_url:
            return

        url = "https://aistudio.google.com/app/prompts/new_chat"
        self.driver.get(url)
        self._debug_screenshot("open_chat_started")

        wait = WebDriverWait(self.driver, 5)
        wait.until(
            EC.presence_of_element_located(("xpath", "//button[@aria-label='Run']"))
        )

        self._model_current = self._get_model_current()
        self._debug_screenshot("open_chat_finished")

    def _open_model_selection_and_click_all(self):
        self.driver.implicitly_wait(3)
        wait = WebDriverWait(self.driver, 5)
        wait.until(
            EC.presence_of_element_located(
                (
                    "xpath",
                    "//ms-model-carousel[@data-test-id='model-carousel-in-selector']",
                )
            )
        )
        for _ in range(3):
            all_element = wait.until(
                EC.presence_of_element_located(
                    (
                        "xpath",
                        ".//button[@variant='filter-chip' and contains(text(), 'All')]",
                    )
                )
            )
            try:
                all_element.click()
            except ElementClickInterceptedException:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView(true);", all_element
                )
                all_element.click()
            except StaleElementReferenceException:
                time.sleep(1)
        self._debug_screenshot("clicked_all_models")

    def get_models(self):
        """Devuelve los nombres de los modelos disponibles."""
        self.open()
        self._debug_screenshot("before_get_models")

        with self.utils.select_modal("//span[@data-test-id='model-name']"):
            self._open_model_selection_and_click_all()
            wait = WebDriverWait(self.driver, 5)
            model_container = wait.until(
                EC.presence_of_element_located(
                    (
                        "xpath",
                        "//ms-model-carousel[@data-test-id='model-carousel-in-selector']",
                    )
                )
            )
            models = []
            for model in model_container.find_elements(
                "xpath", ".//ms-model-carousel-row"
            ):
                element_title = model.find_element(
                    "xpath", ".//span[@class='model-title-text ellipses']"
                )
                models.append(element_title.text)
            self._debug_screenshot("after_get_models")
            return models

    def select_model(self, model_name: str):
        def did_switch_model_confirmation_appear(driver, timeout=2):
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located(
                        ("xpath", "//div[@id='cdk-overlay-2']")
                    )
                )
                return True
            except TimeoutException:
                return False

        self._debug_screenshot(f"before_select_{model_name}")

        models = self.get_models()
        if model_name not in models:
            self._debug_screenshot("model_not_found")
            raise Exception(f"Modelo {model_name} no encontrado")

        with self.utils.select_modal("//span[@data-test-id='model-name']"):
            self._open_model_selection_and_click_all()
            button = self.driver.find_element(
                "xpath", f"//button[.//span[contains(text(), '{model_name}')]]"
            )
            try:
                button.click()
            except ElementClickInterceptedException:
                self.driver.execute_script("arguments[0].scrollIntoView(true);", button)
                button.click()
            except Exception:
                self._debug_screenshot(f"error_clicking_{model_name}")
                raise

            if did_switch_model_confirmation_appear(self.driver):
                self._debug_screenshot(f"confirmation_modal_{model_name}")
                return False

            self._debug_screenshot(f"after_select_{model_name}")
            return True

    @contextmanager
    def _get_element_thinking_mode(self) -> Generator[Optional[WebElement], None, None]:
        self.open()
        try:
            xpath = "//button[@aria-label='Toggle thinking mode']"
            element_toggle = self.driver.find_elements("xpath", xpath)
            yield element_toggle[0] if element_toggle else None
        except:
            yield None

    @property
    def thinking_mode_available(self):
        with self._get_element_thinking_mode() as element_toggle:
            available = (
                element_toggle is not None
                and element_toggle.get_attribute("disabled") is None
            )
            self._debug_screenshot("thinking_mode_available")
            return available

    @property
    def thinking_mode(self) -> bool:
        with self._get_element_thinking_mode() as element_toggle:
            if element_toggle is None:
                return False
            mode = element_toggle.get_attribute("aria-checked") == "true"
            self._debug_screenshot(f"thinking_mode_{'on' if mode else 'off'}")
            return mode

    def toggle_thinking_mode(self):
        with self._get_element_thinking_mode() as element_toggle:
            if element_toggle is None:
                return
            element_toggle.click()
            self._debug_screenshot("toggled_thinking_mode")

    def write_prompt(self, prompt: str, thinking_mode: bool = False):
        self.open()
        self._debug_screenshot("before_write_prompt")

        if self.thinking_mode != thinking_mode:
            self.toggle_thinking_mode()

        textarea = self.driver.find_element("xpath", "//textarea")
        textarea.send_keys(prompt)
        self._debug_screenshot("prompt_written")

        textarea.send_keys(Keys.CONTROL + Keys.ENTER)
        self._wait_loading_generation()

        last_response = self.get_last_response()
        chat_id = self._get_chat_id()
        self._debug_screenshot("after_prompt_response")

        return last_response, chat_id

    def get_last_response(self):
        xpath = "//ms-chat-turn[@class='ng-star-inserted']"
        elements = self.driver.find_elements("xpath", xpath)
        element = elements[-1]
        self._debug_screenshot("get_last_response")
        return element.text.strip()

    def _is_pattern_chat_id(self, string):
        regex = "^[A-Za-z0-9_-]{20,60}$"
        return re.match(regex, string)

    def _get_chat_id(self):
        url = self.driver.current_url
        chat_id = url.split("/")[-1]
        while not self._is_pattern_chat_id(chat_id):
            self._debug_screenshot("waiting_chat_id")
            url = self.driver.current_url
            chat_id = url.split("/")[-1]
            time.sleep(1)
        self._debug_screenshot("chat_id_found")
        return chat_id

    def _wait_loading_generation(self):
        xpath = "//div[@class='loading-indicator generating']"
        wait = WebDriverWait(self.driver, 5)
        wait.until(EC.invisibility_of_element_located(("xpath", xpath)))
        self._debug_screenshot("generation_done")

    def delete_chat(self):
        self._debug_screenshot("before_delete_chat")

        xpath = "//button[@aria-label='View more actions']"
        wait = WebDriverWait(self.driver, 5)
        element = wait.until(EC.presence_of_element_located(("xpath", xpath)))
        element.click()

        xpath = "//button[@aria-label='Delete prompt']"
        element = wait.until(EC.presence_of_element_located(("xpath", xpath)))
        element.click()

        xpath = "//button[@class='ms-button-primary' and @cdkfocusinitial]"
        element = wait.until(EC.presence_of_element_located(("xpath", xpath)))
        element.click()

        xpath = "//h2[@id='mat-mdc-dialog-title-2']"
        wait.until(EC.invisibility_of_element_located(("xpath", xpath)))

        self._debug_screenshot("after_delete_chat")

    def attach_file(self, file_path: Union[str, Path]):
        def wait_loading_file():
            wait = WebDriverWait(self.driver, 5)
            wait.until(
                EC.invisibility_of_element_located(
                    ("xpath", "//*[@role='progressbar']")
                )
            )
            wait.until(
                EC.invisibility_of_element_located(
                    ("xpath", "//mat-icon[contains(text(), 'progress_activity ')]")
                )
            )
            self._debug_screenshot("file_loaded")

        self._debug_screenshot("before_attach_file")

        button = self.driver.find_element(
            "xpath",
            "//button[@aria-label='Insert assets such as images, videos, files, or audio']",
        )
        button.click()
        input = self.driver.find_element(
            "xpath",
            "//div[@class='mat-mdc-menu-content']//button[@aria-label='Upload File']//input",
        )
        input.send_keys(str(file_path))
        wait_loading_file()
        self.utils.simulate_escape()
        self._debug_screenshot("after_attach_file")

    def go_to_chat(self, chat_id: str):
        url = f"https://aistudio.google.com/app/prompts/{chat_id}"
        self.driver.get(url)
        self._debug_screenshot("go_to_chat")
