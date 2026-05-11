from selenium import webdriver
import time
options = webdriver.ChromeOptions()
options.add_argument('--headless=new')
driver = webdriver.Chrome(options=options)
for w in [1, 2, 3]:
    try:
        driver.get(f'https://www.naukri.com/machine-learning-engineer-jobs?wfhType={w}')
        time.sleep(3)
        print(f"wfhType={w}:", driver.title)
    except:
        pass
driver.quit()
