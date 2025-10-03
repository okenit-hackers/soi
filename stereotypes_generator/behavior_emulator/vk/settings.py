import os

LIKE_XPATH = os.environ.get('BEHAVIOR_EMULATOR_VK_LIKE_XPATH', '//a[@class="like_btn like _like"]')
PUBLIC_SUBSCRIBE_XPATH = os.environ.get('BEHAVIOR_EMULATOR_VK_PUBLIC_SUBSCRIBE_XPATH', '//button[@id="public_subscribe"]')
SWITCH_TO_ENG_XPATH = os.environ.get(
 'BEHAVIOR_EMULATOR_VK_SWITCH_TO_ENG_XPATH', '//a[contains(text(), "Switch to English")]'
)
PUBLIC_UNSUBSCRIBE_XPATH = os.environ.get('BEHAVIOR_EMULATOR_VK_PUBLIC_UNSUBSCRIBE_XPATH', '//div[@class="page_actions_inner"]/a[1]')
PUBLICS_XPATH = os.environ.get('BEHAVIOR_EMULATOR_VK_PUBLICS_XPATH', '//h5[@class="post_author"]/a[1]')
EMAIL_INPUT_ID = os.environ.get('BEHAVIOR_EMULATOR_VK_EMAIL_INPUT_ID', 'index_email')
PASSWORD_INPUT_ID = os.environ.get('BEHAVIOR_EMULATOR_VK_PASSWORD_INPUT_ID', 'index_pass')
LOGIN_BUTTON_ID = os.environ.get('BEHAVIOR_EMULATOR_VK_LOGIN_BUTTON_ID', 'index_login_button')
DEFAULT_SLEEP_TIMEOUT = int(os.environ.get('BEHAVIOR_EMULATOR_VK_DEFAULT_SLEEP_TIMEOUT', '5'))