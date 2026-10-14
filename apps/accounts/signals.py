import logging
from django.contrib.auth.signals import user_login_failed
from django.dispatch import receiver

@receiver(user_login_failed)
def log_failed_login(sender, **kwargs):
    logging.getLogger("gym.auth").warning("authentication_failed")
