from django.apps import AppConfig
from django.conf import settings

class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = "System settings"

    def ready(self):
        from django.contrib import admin
        from django.db.models.signals import post_migrate
        gym_name = getattr(settings, "GYM_DISPLAY_NAME", "") or "Gym ERP Administration"
        admin.site.site_header = gym_name
        admin.site.site_title = gym_name
        admin.site.index_title = gym_name

        def on_post_migrate(sender, **kwargs):
            if sender.name == "apps.core":
                from .services import seed_feature_flags
                try:
                    seed_feature_flags()
                except Exception:
                    pass

        post_migrate.connect(on_post_migrate, sender=self)
