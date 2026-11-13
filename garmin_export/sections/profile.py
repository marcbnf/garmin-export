"""Profile: user info, settings, devices, alarms, activity types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..api import safe_call

if TYPE_CHECKING:
    from ..exporter import GarminExporter


def export(ex: GarminExporter) -> None:
    ex.doc.append("\nProfile\n")

    cached = ex.cache.get_section(ex.section_key("profile"))
    if cached is not None and not ex.update_mode:
        data = cached
    else:
        data = {}
        data["full_name"] = safe_call(ex.api.get_full_name, label="full_name")
        data["unit_system"] = safe_call(ex.api.get_unit_system, label="unit_system")
        data["user_profile"] = safe_call(ex.api.get_user_profile, label="user_profile")
        data["profile_settings"] = safe_call(ex.api.get_userprofile_settings, label="profile_settings")
        data["devices"] = safe_call(ex.api.get_devices, label="devices")
        data["primary_device"] = safe_call(ex.api.get_primary_training_device, label="primary_device")
        data["device_alarms"] = safe_call(ex.api.get_device_alarms, label="device_alarms")
        data["last_used_device"] = safe_call(ex.api.get_device_last_used, label="last_used_device")
        data["activity_types"] = safe_call(ex.api.get_activity_types, label="activity_types")
        ex.cache.put_section(ex.section_key("profile"), data)

    if ex.compact:
        ex.doc.append('Schema: "User profile data: full_name, unit_system, user_profile (demographics), profile_settings, devices (paired devices), primary_device, device_alarms, last_used_device, activity_types (supported types)."\n')
        ex.doc.append(f"{ex.doc.json(data)}\n")
    else:
        for title, key in [("Full Name", "full_name"), ("Unit System", "unit_system"),
                           ("User Profile", "user_profile"), ("Profile Settings", "profile_settings"),
                           ("Devices", "devices"), ("Primary Training Device", "primary_device"),
                           ("Device Alarms", "device_alarms"), ("Last Used Device", "last_used_device"),
                           ("Activity Types", "activity_types")]:
            ex.doc.section(title, data.get(key))
