---
name: Selenium Advanced POM Framework
description: 高级 Selenium WebDriver 框架,包含三种页面对象模型模式(Basic POM、Improved POM、Page Factory)、重试机制、Allure 报告、Excel 数据驱动测试和 Selenoid grid 支持。
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e]
frameworks: [selenium]
languages: [java]
domains: [web]
info: vip.hctestedu.com
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
githubUrl: https://github.com/PramodDutta/ATB11xSeleniumAdvanceFramework
---

# Selenium 高级 POM 框架技能

你是一位专注于高级 Selenium WebDriver Java 框架的 QA 自动化专家。当用户要求你构建、审查或调试 Selenium 测试自动化框架时,请遵循以下详细说明,涵盖三种页面对象模型模式、重试机制、数据驱动测试和云 grid 执行。

## 核心原则

1. **三种 POM 模式** -- 根据项目需要实现 Basic POM、Improved POM(带继承)和 Page Factory。
2. **CommonToAllPage 基类** -- 在所有页面对象扩展的单个基类中集中可重用页面操作(click、type、getText)。
3. **DriverManager 单例** -- 通过集中式 DriverManager 管理 WebDriver 生命周期,支持静态 getter/setter 和多浏览器支持。
4. **监听器驱动的报告** -- 使用 TestNG 监听器(ITestListener、IRetryAnalyzer、IAnnotationTransformer)进行自动截图、重试逻辑和 Allure 集成。
5. **数据外部化** -- 将测试数据存储在 properties 文件和 Excel 电子表格中,永远不要硬编码在测试方法中。
6. **等待策略层次** -- 优先使用 WebDriverWait 的显式等待,使用 fluent waits 进行轮询场景,完全避免 Thread.sleep。
7. **环境特定套件** -- 为 QA、staging 和生产环境维护单独的 TestNG XML 套件文件。
8. **Grid 就绪架构** -- 设计框架以在本地或 Selenoid/Docker grid 上运行,无需代码更改。

## 项目结构

```
src/
  main/java/com/thetestingacademy/
    base/
      CommonToAllPage.java            # Base class for all page objects
    driver/
      DriverManager.java              # WebDriver lifecycle management
    pages/
      pageFactory/vwo/
        LoginPage_PF.java             # Page Factory pattern (@FindBy)
        DashBoardPage_PF.java
      pageObjectModel/
        normal_POM/normal_POM/vwo/
          LoginPage.java              # Basic POM pattern
          DashBoardPage.java
          ForgetPasswordPage.java
          FreeTrial.java
          SupportPage.java
        normal_POM/imporved_POM/vwo/
          LoginPage.java              # Improved POM (extends CommonToAllPage)
          DashBoardPage.java
    utils/
      PropertiesReader.java           # Config from .properties files
      WaitHelpers.java                # Explicit, Fluent, Implicit waits
  main/resources/
    data.properties                   # Test configuration & credentials
    log4j2.xml                        # Logging configuration
  test/java/com/thetestingacademy/
    base/
      CommonToAllTest.java            # Base test class (setUp/tearDown)
    listeners/
      RetryAnalyzer.java              # IRetryAnalyzer implementation
      RetryListener.java              # IAnnotationTransformer for global retry
      ScreenshotListener.java         # Screenshot on failure + Allure attach
    tests/
      sample/
        TestCaseBoilerPlate.java      # Test template
      pageFactoryTests/vwo/
        TestVWOLogin_PF.java          # Page Factory tests
      pageObjectModelTests/vwo/
        TestVWOLogin_01_NormalScript_POM.java
        TestVWOLogin_02_PropertyReader_DriverManager_POM_CommonToAll.java
        TestVWOLogin_03_Retry.java    # Tests with retry logic
    utilexcel/
      UtilExcel.java                  # Apache POI Excel reader
  test/resources/
    TestData.xlsx                     # Excel test data
testng_vwo_normal_s1.xml              # Basic test suite
testng_vwo_qa.xml                     # QA environment suite
testng_vwo_prod.xml                   # Production suite
testng_vwo_retry.xml                  # Retry + listeners suite
pom.xml
```

## Maven 依赖

```xml
<dependencies>
    <dependency>
        <groupId>org.seleniumhq.selenium</groupId>
        <artifactId>selenium-java</artifactId>
        <version>4.31.0</version>
    </dependency>
    <dependency>
        <groupId>org.testng</groupId>
        <artifactId>testng</artifactId>
        <version>7.11.0</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>io.qameta.allure</groupId>
        <artifactId>allure-testng</artifactId>
        <version>2.26.0</version>
    </dependency>
    <dependency>
        <groupId>org.uncommons</groupId>
        <artifactId>reportng</artifactId>
        <version>1.1.2</version>
    </dependency>
    <dependency>
        <groupId>org.assertj</groupId>
        <artifactId>assertj-core</artifactId>
        <version>3.25.1</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.apache.poi</groupId>
        <artifactId>poi-ooxml</artifactId>
        <version>5.2.4</version>
    </dependency>
    <dependency>
        <groupId>org.apache.logging.log4j</groupId>
        <artifactId>log4j-core</artifactId>
        <version>3.0.0-beta2</version>
    </dependency>
</dependencies>

<build>
    <plugins>
        <plugin>
            <artifactId>maven-compiler-plugin</artifactId>
            <version>3.11.0</version>
            <configuration>
                <source>11</source>
                <target>11</target>
            </configuration>
        </plugin>
        <plugin>
            <artifactId>maven-surefire-plugin</artifactId>
            <version>3.2.5</version>
            <configuration>
                <suiteXmlFiles>
                    <suiteXmlFile>testng.xml</suiteXmlFile>
                </suiteXmlFiles>
            </configuration>
        </plugin>
    </plugins>
</build>
```

## 驱动管理

```java
package com.thetestingacademy.driver;

import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.edge.EdgeDriver;
import org.openqa.selenium.firefox.FirefoxDriver;

public class DriverManager {
    private static WebDriver driver;

    public static WebDriver getDriver() {
        return driver;
    }

    public static void setDriver(WebDriver driver) {
        DriverManager.driver = driver;
    }

    public static void init() {
        String browser = PropertiesReader.readKey("browser");
        switch (browser.toLowerCase()) {
            case "chrome":
                ChromeOptions chromeOptions = new ChromeOptions();
                chromeOptions.addArguments("--guest");
                driver = new ChromeDriver(chromeOptions);
                break;
            case "edge":
                driver = new EdgeDriver();
                break;
            case "firefox":
                driver = new FirefoxDriver();
                break;
            default:
                driver = new ChromeDriver();
        }
        driver.manage().window().maximize();
    }

    public static void down() {
        if (driver != null) {
            driver.quit();
            driver = null;
        }
    }
}
```

## 页面对象模型 -- 模式 1: Basic POM

最简单的 POM 模式,其中每个页面类拥有自己的定位器并直接使用 `driver.findElement()`。

```java
package com.thetestingacademy.pages.pageObjectModel.normal_POM.normal_POM.vwo;

import com.thetestingacademy.utils.PropertiesReader;
import com.thetestingacademy.utils.WaitHelpers;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;

public class LoginPage {
    private WebDriver driver;

    // Locators
    private By username = By.id("login-username");
    private By password = By.id("login-password");
    private By signButton = By.id("js-login-btn");
    private By error_message = By.cssSelector("[data-qa='error-text']");

    public LoginPage(WebDriver driver) {
        this.driver = driver;
    }

    public String loginToVWOLoginInvalidCreds(String user, String pwd) {
        driver.get(PropertiesReader.readKey("url"));
        driver.findElement(username).sendKeys(user);
        driver.findElement(password).sendKeys(pwd);
        driver.findElement(signButton).click();
        WaitHelpers.checkVisibility(driver, error_message, 3);
        return driver.findElement(error_message).getText();
    }

    public void loginToVWOLoginValidCreds(String user, String pwd) {
        driver.get(PropertiesReader.readKey("url"));
        driver.findElement(username).sendKeys(user);
        driver.findElement(password).sendKeys(pwd);
        driver.findElement(signButton).click();
    }
}
```

## 页面对象模型 -- 模式 2: Improved POM(继承)

扩展 `CommonToAllPage` 以重用常见操作如 `clickElement()`、`enterInput()`、`getText()`。消除重复的 `driver.findElement()` 调用。

```java
package com.thetestingacademy.pages.pageObjectModel.normal_POM.imporved_POM.vwo;

import com.thetestingacademy.base.CommonToAllPage;
import com.thetestingacademy.utils.WaitHelpers;
import org.openqa.selenium.By;

public class LoginPage extends CommonToAllPage {
    private By username = By.id("login-username");
    private By password = By.id("login-password");
    private By signButton = By.id("js-login-btn");
    private By error_message = By.cssSelector("[data-qa='error-text']");

    public String loginToVWOLoginInvalidCreds(String user, String pwd) {
        openVWOUrl();
        enterInput(username, user);
        enterInput(password, pwd);
        clickElement(signButton);
        WaitHelpers.checkVisibility(getDriver(), error_message);
        return getText(error_message);
    }
}
```

## 页面对象模型 -- 模式 3: Page Factory

使用 Selenium 的 `@FindBy` 注解进行声明式元素定位。元素通过 `PageFactory.initElements()` 自动初始化。

```java
package com.thetestingacademy.pages.pageFactory.vwo;

import com.thetestingacademy.base.CommonToAllPage;
import com.thetestingacademy.utils.PropertiesReader;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.FindBy;
import org.openqa.selenium.support.PageFactory;

public class LoginPage_PF extends CommonToAllPage {

    @FindBy(id = "login-username")
    private WebElement username;

    @FindBy(name = "password")
    private WebElement password;

    @FindBy(id = "js-login-btn")
    private WebElement signButton;

    @FindBy(css = "[data-qa='error-text']")
    private WebElement error_message;

    public LoginPage_PF(WebDriver driver) {
        PageFactory.initElements(driver, this);
    }

    public String loginToVWOInvalidCreds() {
        openVWOUrl();
        enterInput(username, PropertiesReader.readKey("invalid_username"));
        enterInput(password, PropertiesReader.readKey("invalid_password"));
        clickElement(signButton);
        return getText(error_message);
    }
}
```

## CommonToAllPage -- 基页面对象

```java
package com.thetestingacademy.base;

import com.thetestingacademy.driver.DriverManager;
import com.thetestingacademy.utils.PropertiesReader;
import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;

public class CommonToAllPage {

    public WebDriver getDriver() {
        return DriverManager.getDriver();
    }

    public void clickElement(By by) {
        getDriver().findElement(by).click();
    }

    public void clickElement(WebElement element) {
        element.click();
    }

    public void enterInput(By by, String text) {
        getDriver().findElement(by).sendKeys(text);
    }

    public void enterInput(WebElement element, String text) {
        element.sendKeys(text);
    }

    public String getText(By by) {
        return getDriver().findElement(by).getText();
    }

    public String getText(WebElement element) {
        return element.getText();
    }

    public void openVWOUrl() {
        getDriver().get(PropertiesReader.readKey("url"));
    }

    public void openOrangeHRMUrl() {
        getDriver().get(PropertiesReader.readKey("ohr_url"));
    }
}
```

## CommonToAllTest -- 基测试类

```java
package com.thetestingacademy.base;

import com.thetestingacademy.driver.DriverManager;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.openqa.selenium.WebDriver;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;

public class CommonToAllTest {
    protected WebDriver driver;
    protected Logger logger = LogManager.getLogger(this.getClass());

    public WebDriver getDriver() {
        return DriverManager.getDriver();
    }

    @BeforeMethod
    public void setUp() {
        DriverManager.init();
        driver = DriverManager.getDriver();
    }

    @AfterMethod
    public void tearDown() {
        DriverManager.down();
    }
}
```

## 等待辅助

```java
package com.thetestingacademy.utils;

import org.openqa.selenium.By;
import org.openqa.selenium.NoSuchElementException;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.FluentWait;
import org.openqa.selenium.support.ui.WebDriverWait;

import java.time.Duration;
import java.util.concurrent.TimeUnit;

public class WaitHelpers {

    // Implicit Wait
    public static void waitImplicitWait(WebDriver driver, int timeInSeconds) {
        driver.manage().timeouts().implicitlyWait(timeInSeconds, TimeUnit.SECONDS);
    }

    // Explicit Wait -- Visibility
    public static void checkVisibility(WebDriver driver, By locator, int timeInSeconds) {
        new WebDriverWait(driver, Duration.ofSeconds(timeInSeconds))
            .until(ExpectedConditions.visibilityOfElementLocated(locator));
    }

    // Explicit Wait -- Visibility (default 10s)
    public static void checkVisibility(WebDriver driver, By locator) {
        new WebDriverWait(driver, Duration.ofSeconds(10))
            .until(ExpectedConditions.visibilityOfElementLocated(locator));
    }

    // Explicit Wait -- Text Present
    public static void checkVisibilityOfAndTextToBePresentInElement(
            WebDriver driver, By locator, String text, int timeInSeconds) {
        new WebDriverWait(driver, Duration.ofSeconds(timeInSeconds))
            .until(ExpectedConditions.textToBePresentInElementLocated(locator, text));
    }

    // Explicit Wait -- Presence
    public static WebElement presenceOfElement(WebDriver driver, By locator, int timeInSeconds) {
        return new WebDriverWait(driver, Duration.ofSeconds(timeInSeconds))
            .until(ExpectedConditions.presenceOfElementLocated(locator));
    }

    // Fluent Wait
    public static void checkVisibilityByFluentWait(WebDriver driver, By locator) {
        new FluentWait<>(driver)
            .withTimeout(Duration.ofSeconds(30))
            .pollingEvery(Duration.ofMillis(500))
            .ignoring(NoSuchElementException.class)
            .until(ExpectedConditions.visibilityOfElementLocated(locator));
    }

    // JVM Sleep (use sparingly)
    public static void waitJVM(int timeInMillis) {
        try {
            Thread.sleep(timeInMillis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
```

## 属性读取器

```java
package com.thetestingacademy.utils;

import java.io.FileInputStream;
import java.io.IOException;
import java.util.Properties;

public class PropertiesReader {
    public static String readKey(String key) {
        Properties properties = new Properties();
        try {
            FileInputStream fis = new FileInputStream("src/main/resources/data.properties");
            properties.load(fis);
        } catch (IOException e) {
            throw new RuntimeException("Failed to read properties file", e);
        }
        return properties.getProperty(key);
    }
}
```

## 配置 -- data.properties

```properties
# Application URLs
url=https://app.vwo.com
ohr_url=https://awesomeqa.com/hr/web/index.php/auth/login
katalon_url=https://katalon-demo-cura.herokuapp.com/

# Credentials
username=user@example.com
password=SecurePass123
invalid_username=admin@admin.com
invalid_password=Test@2024
error_message=Your email, password, IP address or location did not match

# Browser
browser=Chrome

# Expected values
expected_username=Test User
```

## 编写测试 -- Basic POM 测试

```java
package com.thetestingacademy.tests.pageObjectModelTests.vwo;

import com.thetestingacademy.base.CommonToAllTest;
import com.thetestingacademy.pages.pageObjectModel.normal_POM.normal_POM.vwo.DashBoardPage;
import com.thetestingacademy.pages.pageObjectModel.normal_POM.normal_POM.vwo.LoginPage;
import com.thetestingacademy.utils.PropertiesReader;
import io.qameta.allure.Description;
import io.qameta.allure.Owner;
import org.testng.Assert;
import org.testng.annotations.Test;

import static org.assertj.core.api.Assertions.assertThat;

public class TestVWOLogin_01_NormalScript_POM extends CommonToAllTest {

    @Description("Verify that with invalid email and password, error message is shown")
    @Owner("Promode")
    @Test
    public void test_negative_vwo_login() {
        LoginPage loginPage = new LoginPage(driver);
        String error_msg = loginPage.loginToVWOLoginInvalidCreds(
            PropertiesReader.readKey("invalid_username"),
            PropertiesReader.readKey("invalid_password")
        );

        assertThat(error_msg).isNotNull().isNotBlank().isNotEmpty();
        Assert.assertEquals(error_msg, PropertiesReader.readKey("error_message"));
    }

    @Test
    public void testLoginPositiveVWO() {
        LoginPage loginPage = new LoginPage(driver);
        loginPage.loginToVWOLoginValidCreds(
            PropertiesReader.readKey("username"),
            PropertiesReader.readKey("password")
        );

        DashBoardPage dashBoardPage = new DashBoardPage(driver);
        String usernameLoggedIn = dashBoardPage.loggedInUserName();
        Assert.assertEquals(usernameLoggedIn, PropertiesReader.readKey("expected_username"));
    }
}
```

## 编写测试 -- Page Factory 测试

```java
package com.thetestingacademy.tests.pageFactoryTests.vwo;

import com.thetestingacademy.base.CommonToAllTest;
import com.thetestingacademy.pages.pageFactory.vwo.LoginPage_PF;
import com.thetestingacademy.utils.PropertiesReader;
import org.testng.Assert;
import org.testng.annotations.Test;

public class TestVWOLogin_PF extends CommonToAllTest {

    @Test
    public void testLoginNegativeVWO_PF() {
        logger.info("Starting the Page Factory test");
        LoginPage_PF loginPage_PF = new LoginPage_PF(driver);
        String error_msg = loginPage_PF.loginToVWOInvalidCreds();
        logger.info("Error msg: " + error_msg);
        Assert.assertEquals(error_msg, PropertiesReader.readKey("error_message"));
    }
}
```

## 使用 Excel 的数据驱动测试

```java
package com.thetestingacademy.utilexcel;

import org.apache.poi.ss.usermodel.*;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;

import java.io.FileInputStream;
import java.io.IOException;

public class UtilExcel {

    public static Object[][] getTestDataFromExcel(String sheetName) {
        Object[][] data = null;
        try {
            FileInputStream fis = new FileInputStream("src/test/resources/TestData.xlsx");
            Workbook workbook = new XSSFWorkbook(fis);
            Sheet sheet = workbook.getSheet(sheetName);

            int rowCount = sheet.getPhysicalNumberOfRows();
            int colCount = sheet.getRow(0).getPhysicalNumberOfCells();

            data = new Object[rowCount - 1][colCount]; // skip header row

            for (int i = 1; i < rowCount; i++) {
                Row row = sheet.getRow(i);
                for (int j = 0; j < colCount; j++) {
                    Cell cell = row.getCell(j);
                    data[i - 1][j] = getCellValue(cell);
                }
            }
            workbook.close();
        } catch (IOException e) {
            throw new RuntimeException("Failed to read Excel file", e);
        }
        return data;
    }

    private static Object getCellValue(Cell cell) {
        switch (cell.getCellType()) {
            case STRING:
                return cell.getStringCellValue();
            case NUMERIC:
                return cell.getNumericCellValue();
            case BOOLEAN:
                return cell.getBooleanCellValue();
            default:
                return "";
        }
    }
}
```

### 在测试中使用 Excel 数据提供者

```java
@DataProvider(name = "loginData")
public Object[][] getLoginData() {
    return UtilExcel.getTestDataFromExcel("LoginData");
}

@Test(dataProvider = "loginData")
public void testDataDrivenLogin(String email, String password, String expectedResult) {
    LoginPage loginPage = new LoginPage(driver);
    String result = loginPage.loginToVWOLoginInvalidCreds(email, password);
    Assert.assertEquals(result, expectedResult);
}
```

## 重试机制

### RetryAnalyzer

```java
package com.thetestingacademy.listeners;

import org.testng.IRetryAnalyzer;
import org.testng.ITestResult;

public class RetryAnalyzer implements IRetryAnalyzer {
    private int retryCount = 0;
    private static final int maxRetryCount = 1;

    @Override
    public boolean retry(ITestResult iTestResult) {
        if (retryCount < maxRetryCount) {
            retryCount++;
            return true;
        }
        return false;
    }
}
```

### RetryListener(全局重试)

```java
package com.thetestingacademy.listeners;

import org.testng.IAnnotationTransformer;
import org.testng.annotations.ITestAnnotation;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;

public class RetryListener implements IAnnotationTransformer {
    @Override
    public void transform(ITestAnnotation annotation, Class testClass,
                         Constructor testConstructor, Method testMethod) {
        annotation.setRetryAnalyzer(RetryAnalyzer.class);
    }
}
```

## 带 Allure 的截图监听器

```java
package com.thetestingacademy.listeners;

import com.thetestingacademy.driver.DriverManager;
import io.qameta.allure.Allure;
import org.apache.commons.io.FileUtils;
import org.openqa.selenium.OutputType;
import org.openqa.selenium.TakesScreenshot;
import org.openqa.selenium.WebDriver;
import org.testng.ITestListener;
import org.testng.ITestResult;
import org.testng.Reporter;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.Date;

public class ScreenshotListener implements ITestListener {

    @Override
    public void onTestFailure(ITestResult result) {
        WebDriver driver = DriverManager.getDriver();
        String methodName = result.getName();
        String timestamp = new SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date());

        if (driver != null) {
            try {
                File scrFile = ((TakesScreenshot) driver).getScreenshotAs(OutputType.FILE);
                String screenshotPath = "failure_screenshots/" + methodName + "_" + timestamp + ".png";
                FileUtils.copyFile(scrFile, new File(screenshotPath));

                Reporter.log("<a href='" + screenshotPath + "'> Screenshot</a>");
                Allure.addAttachment("Screenshot on Failure", "image/png",
                    new java.io.FileInputStream(screenshotPath), "png");
            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }

    @Override
    public void onTestStart(ITestResult result) {
        System.out.println("Starting test: " + result.getName());
    }

    @Override
    public void onTestSuccess(ITestResult result) {
        System.out.println("Test passed: " + result.getName());
    }
}
```

## TestNG XML 配置

### 基本套件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="VWO Basic Suite">
    <test verbose="2" preserve-order="true" name="BasicPOMTests">
        <classes>
            <class name="com.thetestingacademy.tests.pageObjectModelTests.vwo.TestVWOLogin_01_NormalScript_POM"/>
        </classes>
    </test>
</suite>
```

### 带监听器的重试套件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="Retry Suite">
    <listeners>
        <listener class-name="com.thetestingacademy.listeners.RetryListener"/>
        <listener class-name="com.thetestingacademy.listeners.ScreenshotListener"/>
    </listeners>
    <test verbose="2" preserve-order="true" name="RetryTests">
        <classes>
            <class name="com.thetestingacademy.tests.pageObjectModelTests.vwo.TestVWOLogin_03_Retry"/>
        </classes>
    </test>
</suite>
```

### 多环境套件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="QA Environment Suite" parallel="methods" thread-count="2">
    <parameter name="browser" value="chrome"/>
    <parameter name="environment" value="qa"/>
    <test verbose="2" name="QA Smoke Tests">
        <groups>
            <run>
                <include name="smoke"/>
            </run>
        </groups>
        <classes>
            <class name="com.thetestingacademy.tests.pageObjectModelTests.vwo.TestVWOLogin_01_NormalScript_POM"/>
            <class name="com.thetestingacademy.tests.pageFactoryTests.vwo.TestVWOLogin_PF"/>
        </classes>
    </test>
</suite>
```

## Selenoid Docker Grid 集成

```java
// Remote WebDriver configuration for Selenoid
import org.openqa.selenium.remote.DesiredCapabilities;
import org.openqa.selenium.remote.RemoteWebDriver;

public static void initRemote(String browser) {
    DesiredCapabilities capabilities = new DesiredCapabilities();
    capabilities.setBrowserName(browser);
    capabilities.setVersion("latest");
    capabilities.setCapability("enableVNC", true);
    capabilities.setCapability("enableVideo", true);

    try {
        driver = new RemoteWebDriver(
            new URL("http://localhost:4444/wd/hub"),
            capabilities
        );
    } catch (MalformedURLException e) {
        throw new RuntimeException("Invalid Selenoid hub URL", e);
    }
    driver.manage().window().maximize();
}
```

### Selenoid docker-compose.yml

```yaml
version: '3'
services:
  selenoid:
    image: aerokube/selenoid:latest
    ports:
      - "4444:4444"
    volumes:
      - "./browsers.json:/etc/selenoid/browsers.json"
      - "/var/run/docker.sock:/var/run/docker.sock"
  selenoid-ui:
    image: aerokube/selenoid-ui:latest
    ports:
      - "8080:8080"
    command: ["--selenoid-uri", "http://selenoid:4444"]
```

## Allure 报告

### 注解

```java
@Test
@Description("Verify login with invalid credentials shows error")
@Owner("Promode")
@Severity(SeverityLevel.CRITICAL)
@Story("Login Validation")
@Feature("Authentication")
public void testInvalidLogin() {
    Allure.step("Navigate to login page");
    Allure.step("Enter invalid credentials");
    Allure.step("Verify error message");
    // test implementation
}
```

### 生成和打开报告

```bash
# Run tests
mvn clean test -Dsurefire.suiteXmlFiles=testng_vwo_retry.xml

# Generate Allure report
mvn allure:report

# Or use Allure CLI
allure generate target/allure-results --clean -o allure-report
allure open allure-report
```

## Log4j2 配置

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Configuration status="WARN">
    <Appenders>
        <Console name="Console" target="SYSTEM_OUT">
            <PatternLayout pattern="%d{yyyy-MM-dd HH:mm:ss} %-5p %c{1} - %m%n"/>
        </Console>
        <File name="FileLogger" fileName="logs/test.log">
            <PatternLayout pattern="%d{yyyy-MM-dd HH:mm:ss} %-5p %c{1} - %m%n"/>
        </File>
    </Appenders>
    <Loggers>
        <Root level="info">
            <AppenderRef ref="Console"/>
            <AppenderRef ref="FileLogger"/>
        </Root>
    </Loggers>
</Configuration>
```

## 最佳实践

1. **选择正确的 POM 模式** -- 小项目使用 Basic POM,需要代码重用的中型项目使用 Improved POM,有许多元素的大型项目使用 Page Factory。
2. **集中驱动管理** -- 始终使用 DriverManager 创建和销毁 WebDriver 实例,永远不要在测试类中直接实例化驱动。
3. **外部化所有测试数据** -- 使用 `data.properties` 进行配置,使用 `TestData.xlsx` 进行参数化测试数据。永远不要硬编码 URL、凭证或预期值。
4. **战略性地使用显式等待** -- 将等待放在页面对象中,而不是测试类中。优先使用 `WebDriverWait` 与 `ExpectedConditions` 而不是隐式等待。
5. **为不稳定测试实现重试** -- 使用 `RetryAnalyzer`,最大重试次数为 1-2。通过 TestNG XML 中的 `RetryListener` 全局应用。
6. **失败时捕获截图** -- 使用 `ScreenshotListener` 在每次测试失败时自动捕获并附加截图到 Allure 报告。
7. **维护单独的测试套件** -- 创建环境特定的 TestNG XML 文件(QA、staging、prod),其中包含适当的测试组和参数。
8. **使用 AssertJ 进行流畅断言** -- 将 TestNG 的 `Assert.assertEquals` 与 AssertJ 的 `assertThat` 结合使用,以获得可读、可链式的断言。
9. **有意义地记录** -- 在测试类中使用 Log4j2 记录测试步骤,使测试在 CI 中失败时更容易调试。
10. **为 grid 执行设计** -- 通过抽象驱动创建来保持框架的 grid 就绪,以便测试在本地浏览器和 Selenoid/Docker 上相同运行。

## 应避免的反模式

1. **`Thread.sleep()` 用于同步** -- 始终使用带条件的显式等待。Sleep 导致脆弱、缓慢的测试。
2. **方法中硬编码测试数据** -- 提取到 properties 文件或 Excel。硬编码数据使维护困难。
3. **测试类中直接 `driver.findElement()`** -- 始终通过页面对象。测试应该只调用页面对象方法。
4. **在一个项目中混合 POM 模式** -- 选择一种模式(或故意分层)并在整个框架中保持一致。
5. **在 tearDown 中不退出驱动** -- 始终在 `@AfterMethod` 中调用 `DriverManager.down()` 以防止僵尸浏览器进程。
6. **全局隐式等待** -- 它们与显式等待冲突并导致不可预测的超时。只使用显式等待。
7. **单体测试方法** -- 将长测试场景分解为更小、专注的测试方法,并带有清晰的描述。
8. **忽略测试失败截图** -- 始终配置 `ScreenshotListener` 并在报告中附加证据以进行调试。
9. **不使用测试组** -- 用组(smoke、regression、e2e)标记测试,以在环境间选择性地执行。
10. **仅在本地运行测试** -- 尽早设置 Selenoid 或云 grid。仅在本地运行的测试会遗漏跨浏览器问题。