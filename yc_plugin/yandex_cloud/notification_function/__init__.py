"""Deployable Cloud Function that bridges Yandex Monitoring alerts to OpenSRE.

Not imported by the agent — the contents are uploaded to Yandex Cloud and run
there. Kept in the integration package so the bridge lives beside the code that
reads the same cloud, and ships and versions with it.
"""
