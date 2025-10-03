import logging
from random import random, choice, choices, randint
from time import sleep
from typing import List

from lmgs_datasource.selenium_webdriver_factory.selenium_webdriver_factory import SeleniumWebDriverFactory, BrowserEnum
from selenium.common.exceptions import TimeoutException, ElementNotInteractableException, \
 ElementClickInterceptedException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from stereotypes_generator.behavior_emulator.base import BaseBehaviorEmulator
from stereotypes_generator.behavior_emulator.vk.settings import LIKE_XPATH, PUBLIC_SUBSCRIBE_XPATH, \
 PUBLIC_UNSUBSCRIBE_XPATH, PUBLICS_XPATH, EMAIL_INPUT_ID, PASSWORD_INPUT_ID, LOGIN_BUTTON_ID, SWITCH_TO_ENG_XPATH, \
 DEFAULT_SLEEP_TIMEOUT

logger = logging.getLogger(__name__)


class VkBehaviorEmulator(BaseBehaviorEmulator):
 base_url = 'https://vk.com'

 def __init__(self, **kwargs):
  self.username = kwargs.get('phone_number')
  self.password = kwargs.get('password')
  self.driver = SeleniumWebDriverFactory(headless=True).get_driver(browser_name=BrowserEnum.CHROME)
  if kwargs.get('cookies') is None:
   self.login(
    username=self.username,
    password=self.password,
   )
  else:
   self.driver.get(self.base_url)
   for cookie in kwargs.get('cookies'):
    self.driver.add_cookie(cookie)
   self.driver.get(self.base_url)
  self.actions = {
   self._scroll_page: 0.75,
   self._press_like: 0.1,
   self._subscribe_to_any_public: 0.01,
   self._view_any_public: 0.09,
   self._check_messages: 0.05,
  }

 def emulate_behavior(self, count_of_actions: int, **kwargs):
  self.driver.get(self.base_url)
  queue = self.get_queue(count_of_actions)
  while queue:
   action = queue.pop(0)
   logger.info(f'Executing [{action.__name__}] for service VK')
   action()
   sleep(random() * 10)
  cookies = self.driver.get_cookies()
  self.driver.quit()
  return {
   'cookies': cookies,
   # TODO: добавить определение бана и поправить
   'banned': False,
  }

 def get_queue(self, count_of_actions: int):
  return choices(list(self.actions.keys()), weights=list(self.actions.values()), k=count_of_actions)

 def _scroll_page(self):
  self.driver.execute_script('window.scrollTo(0, document.body.scrollHeight);')

 def _press_like(self):
  likes: List[WebElement] = self.driver.find_elements_by_xpath(LIKE_XPATH)
  like_index = randint(1, len(likes))
  self.driver.execute_script(
   f'document.getElementsByClassName("like_btn like _like")[{like_index}].click();'
  )

 def _switch_to_eng_lang(self):
  WebDriverWait(self.driver, DEFAULT_SLEEP_TIMEOUT).until(EC.element_to_be_clickable(
   (By.XPATH, SWITCH_TO_ENG_XPATH))
  ).click()

 def _send_delayed_keys(self, keys: str, element: WebElement):
  for char in keys:
   sleep(random() / 10)
   element.send_keys(char)

 def _public_subscribe(self):
  try:
   subscribe_button = WebDriverWait(
    self.driver, DEFAULT_SLEEP_TIMEOUT).until(
    EC.element_to_be_clickable((By.XPATH, PUBLIC_SUBSCRIBE_XPATH)))
   subscribe_button.click()
  except (TimeoutException, ElementNotInteractableException) as e:
   logger.info(f'Got [{e}] during _public_subscribe')
   self.driver.get(self.base_url + '/feed')

 def _public_unsubscribe(self):
  try:
   unsubscribe_button = WebDriverWait(
    self.driver, DEFAULT_SLEEP_TIMEOUT).until(
    EC.element_to_be_clickable((By.XPATH, PUBLIC_UNSUBSCRIBE_XPATH)))
   unsubscribe_button.click()
  except TimeoutException as e:
   logger.info(f'Got [{e}] during _public_subscribe')

 def _get_publics(self):
  if self.driver.current_url != self.base_url + '/feed':
   self.driver.get(self.base_url)
  return self.driver.find_elements_by_xpath(PUBLICS_XPATH)

 def _subscribe_to_any_public(self):
  self._view_any_public()
  self._public_subscribe()

 def _view_any_public(self):
  publics = self._get_publics()
  try:
   self.driver.get(choice(publics).get_attribute('href'))
   sleep(DEFAULT_SLEEP_TIMEOUT)
  except StaleElementReferenceException as e:
   logger.info(f'Can not follow public link. [{e}]')

 def _check_messages(self):
  self.driver.get(self.base_url + '/im')
  sleep(random() * 20)
  self.driver.get(self.base_url + '/feed')

 def login(self, username: str, password: str):
  logger.info('Login initiating')
  self.driver.get(self.base_url)
  self._switch_to_eng_lang()
  sleep(DEFAULT_SLEEP_TIMEOUT)
  email_input = WebDriverWait(self.driver, DEFAULT_SLEEP_TIMEOUT).until(
   EC.element_to_be_clickable((By.ID, EMAIL_INPUT_ID)))
  email_input.click()
  self._send_delayed_keys(username, email_input)
  password_input = self.driver.find_element_by_id(PASSWORD_INPUT_ID)
  password_input.click()
  self._send_delayed_keys(password, password_input)
  login_button = self.driver.find_element_by_id(LOGIN_BUTTON_ID)
  login_button.click()
  sleep(5)