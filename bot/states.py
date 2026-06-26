from aiogram.fsm.state import State, StatesGroup


class AuthStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()


class CampaignStates(StatesGroup):
    waiting_usernames_text = State()
    waiting_message_text = State()
    waiting_media = State()
    waiting_delay = State()
