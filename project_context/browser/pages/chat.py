import re
import time
from contextlib import contextmanager
from typing import Generator, Optional

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
)
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from project_context.browser.utils import BrowserUtils


class ChatBrowser:
    def __init__(self, driver: webdriver.Chrome):
        self.driver = driver
        self.utils = BrowserUtils(driver)

    @property
    def model_current(self):
        if not hasattr(self, "_model_current"):
            self._model_current = self._get_model_current()
        return self._model_current

    def _get_model_current(self):
        self.open()
        return self.driver.find_element(
            "xpath", "//span[@data-test-id='model-name']"
        ).text.strip()

    def open(self):
        if "prompts" in self.driver.current_url:
            return

        url = "https://aistudio.google.com/app/prompts/new_chat"
        self.driver.get(url)

        wait = WebDriverWait(self.driver, 5)
        wait.until(
            EC.presence_of_element_located(("xpath", "//button[@aria-label='Run']"))
        )

        # actualizar el nombre del modelo
        self._model_current = self._get_model_current()

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

    def get_models(self):
        """Devuelve los nombre de los modelos disponibles."""

        self.open()
        with self.utils.select_modal("//span[@data-test-id='model-name']"):
            self._open_model_selection_and_click_all()
            # Esperar a que se carguen los modelos
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
            return models

    def select_model(self, model_name: str):
        def did_switch_model_confirmation_appear(driver, timeout=2):
            """Si el modal de confirmación de cambio de modelo aparece, devuelve True.

            Los modelos que necesitan un cambio por lo general al cambiar crean un nuevo chat, y aparte su interfaz cambia.
            """
            try:
                WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located(
                        ("xpath", "//div[@id='cdk-overlay-2']")
                    )
                )
                return True
            except TimeoutException:
                return False

        models = self.get_models()
        if model_name not in models:
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

            if did_switch_model_confirmation_appear(self.driver):
                return False

            return True

    @contextmanager
    def _get_element_thinking_mode(self) -> Generator[Optional[WebElement], None, None]:
        """Devuelve un elemento que puede ser clickeado para cambiar el modo de pensamiento para los modelos `siempre pensante`, `pensante o no pensante`.


        Un modelo puede ser:
        - siempre pensante
        - pensante o no pensante
        - nunca pensante

        Yields:
            WebElement: El elemento que puede ser clickeado para cambiar el modo de pensamiento
        """
        self.open()
        try:
            xpath = "//button[@aria-label='Toggle thinking mode']"
            element_toggle = self.driver.find_elements("xpath", xpath)
            if len(element_toggle) == 0:
                yield None
            else:
                yield element_toggle[0]
        except:
            yield None

    @property
    def thinking_mode_available(self):
        """Devuelve True si el modelo actual del chat, puede alterar el modo de pensamiento."""
        with self._get_element_thinking_mode() as element_toggle:
            if element_toggle is None:
                return False
            return element_toggle.get_attribute("disabled") is None

    @property
    def thinking_mode(self) -> bool:
        """Devuelve el modo de pensamiento actual del chat.
        Si el modo de pensamiento no esta disponible o alterar el modo, devuelve False.
        """
        with self._get_element_thinking_mode() as element_toggle:
            if element_toggle is None:
                return False
            return element_toggle.get_attribute("aria-checked") == "true"

    def toggle_thinking_mode(self):
        """Alterna el modo de pensamiento del chat."""
        with self._get_element_thinking_mode() as element_toggle:
            if element_toggle is None:
                return
            element_toggle.click()

    def write_prompt(self, prompt: str, thinking_mode: bool = False):
        self.open()

        if self.thinking_mode != thinking_mode:
            self.toggle_thinking_mode()

        textarea = self.driver.find_element("xpath", "//textarea")

        textarea.send_keys(prompt)
        time.sleep(1)
        textarea.send_keys(Keys.CONTROL + Keys.ENTER)
        self._wait_loading_generation()

        last_response = self.get_last_response()
        chat_id = self._get_chat_id()

        return last_response, chat_id

    def get_last_response(self):
        xpath = "//ms-chat-turn[@class='ng-star-inserted']"
        elements = self.driver.find_elements("xpath", xpath)
        element = elements[-1]
        return element.text.strip()

    def _is_pattern_chat_id(self, string):
        """Verifica si la cadena cumple con el patrón de chat_id.

        Noota: No es una validación exhaustiva, solo verifica la longitud y los caracteres permitidos.
        """
        # regex= "^[A-Za-z0-9-]{20,60}$"
        # ^ y $ - inicio y fin de la cadena.
        # [A-Za-z0-9-] - permite letras (mayúsculas y minúsculas), números y guiones.
        # {20,60} - longitud variable entre 20 y 60 caracteres.
        regex = "^[A-Za-z0-9_-]{20,60}$"
        return re.match(regex, string)

    def _get_chat_id(self):
        url = self.driver.current_url
        chat_id = url.split("/")[-1]
        while not self._is_pattern_chat_id(chat_id):
            url = self.driver.current_url
            chat_id = url.split("/")[-1]
            time.sleep(1)
        return chat_id

    def _wait_loading_generation(self):
        xpath = "//div[@class='loading-indicator generating']"
        wait = WebDriverWait(self.driver, 5)
        wait.until(EC.invisibility_of_element_located(("xpath", xpath)))

    def delete_chat(self):
        """Elimina el chat actual."""
        # click tres puntos
        xpath = "//button[@aria-label='View more actions']"
        wait = WebDriverWait(self.driver, 5)
        element = wait.until(EC.presence_of_element_located(("xpath", xpath)))
        element.click()
        # click borrar
        xpath = "//button[@aria-label='Delete prompt']"
        wait = WebDriverWait(self.driver, 5)
        element = wait.until(EC.presence_of_element_located(("xpath", xpath)))
        element.click()
        # click confirmar
        xpath = "//button[@class='ms-button-primary' and @cdkfocusinitial]"
        wait = WebDriverWait(self.driver, 5)
        element = wait.until(EC.presence_of_element_located(("xpath", xpath)))
        element.click()
        # esperar a que se borre
        xpath = "//h2[@id='mat-mdc-dialog-title-2']"
        wait = WebDriverWait(self.driver, 5)
        wait.until(EC.invisibility_of_element_located(("xpath", xpath)))
