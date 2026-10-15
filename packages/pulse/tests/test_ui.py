from __future__ import annotations

from ai_market_pulse.ui import (
    lang,
    language_boot_script,
    language_runtime_script,
    language_toggle,
    ui_styles,
)


def test_lang_emits_english_and_chinese_spans() -> None:
    html = lang("Buy", "买入")

    assert html == "<span data-i18n-en>Buy</span><span data-i18n-zh>买入</span>"


def test_lang_escapes_html_special_characters_in_both_languages() -> None:
    html = lang(
        'A & B <script> "quoted"',
        '甲 & 乙 <脚本> "引用"',
    )

    assert (
        "<span data-i18n-en>A &amp; B &lt;script&gt; &quot;quoted&quot;</span>" in html
    )
    assert (
        "<span data-i18n-zh>甲 &amp; 乙 &lt;脚本&gt; &quot;引用&quot;</span>" in html
    )
    assert "<script>" not in html
    assert '"quoted"' not in html
    assert "A & B" not in html


def test_language_boot_script_defaults_to_zh_for_zh_cn() -> None:
    script = language_boot_script("zh-CN")

    assert 'browserLang = (navigator.language || "").toLowerCase().indexOf("zh") === 0 ? "zh" : "zh";' in script


def test_language_boot_script_defaults_to_en_for_en_us() -> None:
    script = language_boot_script("en-US")

    assert 'browserLang = (navigator.language || "").toLowerCase().indexOf("zh") === 0 ? "zh" : "en";' in script


def test_language_boot_script_falls_back_to_en_for_unrecognized_input() -> None:
    script = language_boot_script("fr-FR")

    assert 'indexOf("zh") === 0 ? "zh" : "en";' in script


def test_language_boot_script_falls_back_to_en_for_empty_input() -> None:
    script = language_boot_script("")

    assert 'indexOf("zh") === 0 ? "zh" : "en";' in script


def test_language_boot_script_uses_default_parameter_when_omitted() -> None:
    script = language_boot_script()

    assert 'indexOf("zh") === 0 ? "zh" : "en";' in script


def test_language_boot_script_sets_document_lang_dataset_wiring() -> None:
    script = language_boot_script("zh-CN")

    assert "document.documentElement.dataset.lang = lang;" in script
    assert 'document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";' in script
    assert '<script>' in script and '</script>' in script


def test_language_toggle_contains_both_language_buttons() -> None:
    html = language_toggle()

    assert 'data-lang-choice="en"' in html
    assert 'data-lang-choice="zh"' in html
    assert 'class="language-switch"' in html


def test_language_runtime_script_wires_click_handlers_and_storage() -> None:
    script = language_runtime_script()

    assert "localStorage.setItem(\"amp-lang\", lang);" in script
    assert "addEventListener(\"click\"" in script
    assert "querySelectorAll(\"[data-lang-choice]\")" in script


def test_ui_styles_returns_non_empty_css_with_expected_selectors() -> None:
    css = ui_styles()

    assert ":root" in css
    assert "--brand:" in css
    assert 'html[data-lang="en"] [data-i18n-zh]' in css
    assert 'html[data-lang="zh"] [data-i18n-en]' in css
