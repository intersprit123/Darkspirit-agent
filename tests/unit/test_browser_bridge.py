from darkspirit_bridge import __main__ as bridge


def test_extension_origin_requires_chromium_extension_origin():
    assert bridge.EXTENSION_ORIGIN.fullmatch("chrome-extension://abcdefghijklmnopabcdefghijklmnop")
    assert not bridge.EXTENSION_ORIGIN.fullmatch("https://chatgpt.com")
    assert not bridge.EXTENSION_ORIGIN.fullmatch("chrome-extension://not-a-valid-id")


def test_bridge_exposes_full_windows_use_catalog_with_separate_action_gates():
    assert bridge.ACTION_TOOLS == {
        "app_tool",
        "click_tool",
        "type_tool",
        "shortcut_tool",
        "scroll_tool",
        "move_tool",
        "wait_tool",
        "shell_tool",
        "desktop_tool",
        "multi_select_tool",
        "multi_edit_tool",
        "file_tool",
        "memory_tool",
        "move_mouse_continuously",
    }
    assert bridge.READ_ONLY_TOOLS == {"scrape_tool"}
    assert bridge._actions_enabled is False
    assert bridge._screenshots_enabled is False


def test_mouse_path_policy_bounds_continuous_movement():
    assert "move_mouse_continuously" in bridge.ACTION_TOOLS
    assert bridge.MAX_MESSAGE_BYTES >= 2 * 1024 * 1024
    assert bridge.MAX_SCREENSHOT_BYTES <= 2_000_000


def test_safe_label_hides_text_input_values():
    assert bridge._safe_label("a typed secret", "EditControl") == "[input contents hidden]"
    assert bridge._safe_label("enter password", "PasswordControl") == "[input contents hidden]"


def test_safe_label_redacts_common_personal_data():
    assert "[redacted email]" in bridge._safe_label("Contact: student@example.com")
    assert "[redacted number]" in bridge._safe_label("Card number 1234 5678 9012")


def test_voice_bridge_token_is_random_and_not_empty():
    assert isinstance(bridge._voice_token, str)
    assert len(bridge._voice_token) >= 32
