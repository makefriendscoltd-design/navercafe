"""
blockquote test v4 - Selenium-only text input (no pyautogui for paste)
"""
import os, time, re, configparser, urllib.parse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException, UnexpectedAlertPresentException
import pyautogui, pyperclip

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.ini")

def read_config():
    config = configparser.RawConfigParser()
    config.read(CONFIG_FILE, encoding='utf-8')
    return {
        'id': config.get('NAVER', 'id'),
        'pw': config.get('NAVER', 'pw'),
        'cafe_url': config.get('NAVER', 'cafe_url'),
    }

def login_and_open_editor(driver, cfg):
    driver.get("https://nid.naver.com/nidlogin.login")
    time.sleep(2)
    pyperclip.copy(cfg['id'])
    driver.find_element(By.CSS_SELECTOR, "#id").click()
    driver.find_element(By.CSS_SELECTOR, "#id").send_keys(Keys.CONTROL, 'v')
    time.sleep(1)
    pyperclip.copy(cfg['pw'])
    driver.find_element(By.CSS_SELECTOR, "#pw").click()
    driver.find_element(By.CSS_SELECTOR, "#pw").send_keys(Keys.CONTROL, 'v')
    time.sleep(1)
    driver.find_element(By.CSS_SELECTOR, r"#log\.login").click()
    time.sleep(3)
    print("[OK] login")

    decoded = urllib.parse.unquote(cfg['cafe_url'])
    club_match = re.search(r'clubid[=&](\d+)|cafes/(\d+)', decoded)
    menu_match = re.search(r'menuid[=&](\d+)|menus/(\d+)', decoded)
    editor_ready = False
    if club_match and menu_match:
        clubid = club_match.group(1) or club_match.group(2)
        menuid = menu_match.group(1) or menu_match.group(2)
        try:
            driver.get(f"https://cafe.naver.com/ArticleWrite.nhn?m=write&clubid={clubid}&menuid={menuid}")
            time.sleep(3)
            try:
                driver.switch_to.alert.accept()
            except Exception:
                editor_ready = True
        except UnexpectedAlertPresentException:
            try: driver.switch_to.alert.accept()
            except: pass

    if not editor_ready:
        driver.get(cfg['cafe_url'])
        time.sleep(3)
        try: WebDriverWait(driver, 5).until(EC.frame_to_be_available_and_switch_to_it("cafe_main"))
        except: pass
        try: WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#writeFormBtn"))).click()
        except: driver.find_element(By.XPATH, "//*[contains(text(), '\uae00\uc4f0\uae30')]").click()

    time.sleep(5)
    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
        time.sleep(3)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".textarea_input")))
    driver.find_element(By.CSS_SELECTOR, ".textarea_input").send_keys("BQ_TEST")
    time.sleep(0.5)
    # TAB to body
    driver.find_element(By.CSS_SELECTOR, ".textarea_input").send_keys(Keys.TAB)
    time.sleep(1)
    print("[OK] editor ready")


def dump_components(driver):
    """Dump se-component list from se-components-wrap"""
    result = driver.execute_script("""
        var wrap = document.querySelector('.se-components-wrap');
        if (!wrap) return ['no se-components-wrap'];
        var out = [];
        var comps = wrap.children;
        for (var i = 0; i < comps.length; i++) {
            var c = comps[i];
            var cls = c.className || '';
            var type = 'OTHER';
            if (cls.indexOf('se-quotation') >= 0 && cls.indexOf('se-component') >= 0) type = 'QUOTE';
            else if (cls.indexOf('se-text') >= 0 && cls.indexOf('se-component') >= 0) type = 'TEXT';
            else if (cls.indexOf('se-image') >= 0 && cls.indexOf('se-component') >= 0) type = 'IMAGE';
            var text = (c.textContent || '').substring(0, 60).replace(/\\s+/g, ' ').trim();
            out.push('[' + i + '] ' + type + ' | ' + text);
        }
        return out;
    """)
    print("  -- components --")
    for line in result:
        try: print(f"    {line}")
        except UnicodeEncodeError: print(f"    (enc err: {repr(line[:30])})")
    return result


def refocus_editor(driver):
    """Ensure browser window has focus for pyautogui"""
    driver.execute_script("window.focus();")
    time.sleep(0.2)
    # Click on the editor area to ensure it has focus
    try:
        wrap = driver.find_element(By.CSS_SELECTOR, '.se-components-wrap')
        ActionChains(driver).move_to_element(wrap).click().perform()
        time.sleep(0.3)
    except:
        pass


def main():
    cfg = read_config()
    driver = webdriver.Chrome()
    driver.maximize_window()

    try:
        login_and_open_editor(driver, cfg)

        print("\n====== TEST 1: Paste into blockquote with refocus ======")
        # Click blockquote button
        driver.find_element(By.CSS_SELECTOR, ".se-quote-toolbar-button").click()
        time.sleep(1.5)
        try:
            menu = driver.find_element(By.CSS_SELECTOR, ".se-quote-toolbar-menu li button")
            if menu.is_displayed(): menu.click(); time.sleep(1)
        except: pass

        # Try to click on quote content area
        try:
            qc = driver.find_elements(By.CSS_SELECTOR, '.se-quotation-content .se-text-paragraph')
            if qc:
                print(f"  found {len(qc)} quote content paragraphs")
                qc[-1].click()
                time.sleep(0.5)
        except Exception as e:
            print(f"  quote content click failed: {e}")

        # Refocus window then paste
        driver.execute_script("window.focus();")
        time.sleep(0.3)
        pyperclip.copy("HEADING_1")
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(1)

        dump_components(driver)
        h1_found = driver.execute_script("""
            var q = document.querySelectorAll('.se-quotation-content');
            for (var i = 0; i < q.length; i++) {
                if (q[i].textContent.indexOf('HEADING_1') >= 0) return 'in_quote_content';
            }
            return document.body.textContent.indexOf('HEADING_1') >= 0 ? 'in_page' : 'not_found';
        """)
        print(f"  HEADING_1 location: {h1_found}")

        print("\n====== TEST 2: Escape via ActionChains click on wrap ======")
        # Find the se-components-wrap and click at the bottom
        wrap = driver.find_element(By.CSS_SELECTOR, '.se-components-wrap')
        wrap_h = wrap.size['height']
        print(f"  wrap height: {wrap_h}px")

        # Click at bottom of the wrap (below all components)
        ActionChains(driver).move_to_element_with_offset(
            wrap, 0, wrap_h // 2 - 10
        ).click().perform()
        time.sleep(0.5)

        # Now paste body text
        driver.execute_script("window.focus();")
        time.sleep(0.2)
        pyperclip.copy("BODY_OUTSIDE")
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(1)

        dump_components(driver)
        body_found = driver.execute_script("""
            var comps = document.querySelectorAll('.se-components-wrap > *');
            for (var i = 0; i < comps.length; i++) {
                var c = comps[i];
                var isQuote = c.className.indexOf('se-quotation') >= 0;
                if (c.textContent.indexOf('BODY_OUTSIDE') >= 0) {
                    return isQuote ? 'in_QUOTE' : 'in_TEXT';
                }
            }
            return document.body.textContent.indexOf('BODY_OUTSIDE') >= 0 ? 'in_page_elsewhere' : 'not_found';
        """)
        print(f"  BODY_OUTSIDE location: {body_found}")

        print("\n====== TEST 3: Selenium-only paste (no pyautogui) ======")
        # Use Selenium ActionChains for paste instead of pyautogui
        pyperclip.copy("SELENIUM_PASTE_TEST")
        ActionChains(driver).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        time.sleep(1)

        sel_found = driver.execute_script("""
            return document.body.textContent.indexOf('SELENIUM_PASTE_TEST') >= 0 ? 'found' : 'not_found';
        """)
        print(f"  SELENIUM_PASTE_TEST: {sel_found}")

        dump_components(driver)

        print("\n====== TEST 4: execCommand insertText ======")
        driver.execute_script("""
            var sel = window.getSelection();
            if (sel.rangeCount > 0) {
                document.execCommand('insertText', false, 'EXECCOMMAND_TEST');
            }
        """)
        time.sleep(0.5)

        exec_found = driver.execute_script("""
            return document.body.textContent.indexOf('EXECCOMMAND_TEST') >= 0 ? 'found' : 'not_found';
        """)
        print(f"  EXECCOMMAND_TEST: {exec_found}")

        dump_components(driver)

        print("\n====== RESULTS ======")
        print(f"  Heading paste (pyautogui):     {h1_found}")
        print(f"  Body outside (pyautogui):      {body_found}")
        print(f"  Selenium ActionChains paste:   {sel_found}")
        print(f"  execCommand insertText:        {exec_found}")

        print("\nBrowser open 30s for inspection...")
        time.sleep(30)
    finally:
        driver.quit()

if __name__ == '__main__':
    main()
