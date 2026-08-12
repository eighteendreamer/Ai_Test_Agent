---
name: Selenium Java Testing
description: Selenium WebDriver 与 Java 使用 Page Object Model 和 TestNG
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [e2e]
frameworks: [selenium]
info: vip.hctestedu.com
languages: [java]
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Selenium Java 测试技能

您是一位专注于 Selenium WebDriver 与 Java 的 QA 自动化工程师。当用户要求您编写、审查或调试 Selenium Java 测试时，请遵循这些详细说明。

## 核心原则

1. **显式等待优于隐式等待** -- 始终使用 `WebDriverWait` 与 `ExpectedConditions`。
2. **页面对象模型** -- 将所有页面交互封装在页面对象之后。
3. **驱动管理** -- 使用 WebDriverManager 或 Selenium Manager 管理驱动二进制文件。
4. **线程安全** -- 使用 `ThreadLocal<WebDriver>` 进行并行执行。
5. **清洁清理** -- 始终在 `@AfterMethod` 或 `@AfterEach` 中退出驱动。

## 项目结构

```
src/
  main/java/com/example/
    pages/
      BasePage.java
      LoginPage.java
      DashboardPage.java
    utils/
      DriverFactory.java
      ConfigReader.java
      WaitHelper.java
    models/
      User.java
  test/java/com/example/
    tests/
      BaseTest.java
      LoginTest.java
      DashboardTest.java
    dataproviders/
      LoginDataProvider.java
  test/resources/
    config.properties
    testng.xml
pom.xml
```

## Maven 依赖

```xml
<dependencies>
    <dependency>
        <groupId>org.seleniumhq.selenium</groupId>
        <artifactId>selenium-java</artifactId>
        <version>4.18.0</version>
    </dependency>
    <dependency>
        <groupId>org.testng</groupId>
        <artifactId>testng</artifactId>
        <version>7.9.0</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>io.github.bonigarcia</groupId>
        <artifactId>webdrivermanager</artifactId>
        <version>5.7.0</version>
    </dependency>
    <dependency>
        <groupId>org.assertj</groupId>
        <artifactId>assertj-core</artifactId>
        <version>3.25.3</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>com.aventstack</groupId>
        <artifactId>extentreports</artifactId>
        <version>5.1.1</version>
    </dependency>
    <dependency>
        <groupId>org.slf4j</groupId>
        <artifactId>slf4j-simple</artifactId>
        <version>2.0.12</version>
    </dependency>
</dependencies>
```

## 驱动工厂

```java
package com.example.utils;

import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.firefox.FirefoxDriver;
import org.openqa.selenium.firefox.FirefoxOptions;
import org.openqa.selenium.edge.EdgeDriver;

public class DriverFactory {
    private static final ThreadLocal<WebDriver> driverThreadLocal = new ThreadLocal<>();

    public static WebDriver getDriver() {
        return driverThreadLocal.get();
    }

    public static void initDriver(String browser) {
        WebDriver driver;

        switch (browser.toLowerCase()) {
            case "firefox":
                FirefoxOptions ffOptions = new FirefoxOptions();
                if (Boolean.parseBoolean(System.getProperty("headless", "false"))) {
                    ffOptions.addArguments("--headless");
                }
                driver = new FirefoxDriver(ffOptions);
                break;

            case "edge":
                driver = new EdgeDriver();
                break;

            case "chrome":
            default:
                ChromeOptions chromeOptions = new ChromeOptions();
                chromeOptions.addArguments("--disable-gpu");
                chromeOptions.addArguments("--no-sandbox");
                chromeOptions.addArguments("--disable-dev-shm-usage");
                if (Boolean.parseBoolean(System.getProperty("headless", "false"))) {
                    chromeOptions.addArguments("--headless=new");
                }
                driver = new ChromeDriver(chromeOptions);
                break;
        }

        driver.manage().window().maximize();
        driverThreadLocal.set(driver);
    }

    public static void quitDriver() {
        WebDriver driver = driverThreadLocal.get();
        if (driver != null) {
            driver.quit();
            driverThreadLocal.remove();
        }
    }
}
```

## 基础页面对象

```java
package com.example.pages;

import org.openqa.selenium.*;
import org.openqa.selenium.support.PageFactory;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.openqa.selenium.support.ui.Select;

import java.time.Duration;

public abstract class BasePage {
    protected WebDriver driver;
    protected WebDriverWait wait;

    public BasePage(WebDriver driver) {
        this.driver = driver;
        this.wait = new WebDriverWait(driver, Duration.ofSeconds(10));
        PageFactory.initElements(driver, this);
    }

    protected void click(By locator) {
        wait.until(ExpectedConditions.elementToBeClickable(locator)).click();
    }

    protected void type(By locator, String text) {
        WebElement element = wait.until(ExpectedConditions.visibilityOfElementLocated(locator));
        element.clear();
        element.sendKeys(text);
    }

    protected String getText(By locator) {
        return wait.until(ExpectedConditions.visibilityOfElementLocated(locator)).getText();
    }

    protected boolean isDisplayed(By locator) {
        try {
            return wait.until(ExpectedConditions.visibilityOfElementLocated(locator)).isDisplayed();
        } catch (TimeoutException e) {
            return false;
        }
    }

    protected void selectByVisibleText(By locator, String text) {
        WebElement element = wait.until(ExpectedConditions.visibilityOfElementLocated(locator));
        new Select(element).selectByVisibleText(text);
    }

    protected void waitForUrlContains(String urlPart) {
        wait.until(ExpectedConditions.urlContains(urlPart));
    }

    protected void scrollToElement(By locator) {
        WebElement element = driver.findElement(locator);
        ((JavascriptExecutor) driver).executeScript("arguments[0].scrollIntoView(true);", element);
    }

    protected void takeScreenshot(String name) {
        TakesScreenshot ts = (TakesScreenshot) driver;
        byte[] screenshot = ts.getScreenshotAs(OutputType.BYTES);
        // 保存或附加到报告
    }

    public String getTitle() {
        return driver.getTitle();
    }

    public String getCurrentUrl() {
        return driver.getCurrentUrl();
    }
}
```

## 具体页面对象

```java
package com.example.pages;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;

public class LoginPage extends BasePage {
    // 定位器
    private static final By EMAIL_INPUT = By.id("email");
    private static final By PASSWORD_INPUT = By.id("password");
    private static final By LOGIN_BUTTON = By.cssSelector("button[type='submit']");
    private static final By ERROR_MESSAGE = By.cssSelector("[data-testid='error-message']");
    private static final By FORGOT_PASSWORD_LINK = By.linkText("Forgot password?");
    private static final By REMEMBER_ME_CHECKBOX = By.id("remember-me");

    public LoginPage(WebDriver driver) {
        super(driver);
    }

    public LoginPage navigate() {
        driver.get(ConfigReader.getProperty("base.url") + "/login");
        return this;
    }

    public LoginPage enterEmail(String email) {
        type(EMAIL_INPUT, email);
        return this;
    }

    public LoginPage enterPassword(String password) {
        type(PASSWORD_INPUT, password);
        return this;
    }

    public LoginPage checkRememberMe() {
        click(REMEMBER_ME_CHECKBOX);
        return this;
    }

    public DashboardPage clickLogin() {
        click(LOGIN_BUTTON);
        return new DashboardPage(driver);
    }

    public LoginPage clickLoginExpectingError() {
        click(LOGIN_BUTTON);
        return this;
    }

    public DashboardPage loginAs(String email, String password) {
        enterEmail(email);
        enterPassword(password);
        return clickLogin();
    }

    public String getErrorMessage() {
        return getText(ERROR_MESSAGE);
    }

    public boolean isErrorDisplayed() {
        return isDisplayed(ERROR_MESSAGE);
    }

    public ForgotPasswordPage clickForgotPassword() {
        click(FORGOT_PASSWORD_LINK);
        return new ForgotPasswordPage(driver);
    }
}
```

## 基础测试类

```java
package com.example.tests;

import com.example.utils.DriverFactory;
import org.openqa.selenium.WebDriver;
import org.testng.ITestResult;
import org.testng.annotations.*;

public abstract class BaseTest {
    protected WebDriver driver;

    @Parameters({"browser"})
    @BeforeMethod
    public void setUp(@Optional("chrome") String browser) {
        DriverFactory.initDriver(browser);
        driver = DriverFactory.getDriver();
    }

    @AfterMethod
    public void tearDown(ITestResult result) {
        if (result.getStatus() == ITestResult.FAILURE) {
            // 失败时捕获截图
            captureScreenshot(result.getName());
        }
        DriverFactory.quitDriver();
    }

    private void captureScreenshot(String testName) {
        // 截图捕获逻辑
    }
}
```

## 使用 TestNG 编写测试

```java
package com.example.tests;

import com.example.pages.LoginPage;
import com.example.pages.DashboardPage;
import org.testng.annotations.*;
import static org.assertj.core.api.Assertions.*;

public class LoginTest extends BaseTest {
    private LoginPage loginPage;

    @BeforeMethod
    public void navigateToLogin() {
        super.setUp("chrome");
        loginPage = new LoginPage(driver).navigate();
    }

    @Test(description = "使用有效凭据验证成功登录")
    public void testSuccessfulLogin() {
        DashboardPage dashboard = loginPage.loginAs("user@example.com", "SecurePass123!");

        assertThat(dashboard.getCurrentUrl()).contains("/dashboard");
        assertThat(dashboard.getWelcomeMessage()).contains("Welcome");
    }

    @Test(description = "使用无效凭据验证错误消息")
    public void testInvalidLogin() {
        loginPage.enterEmail("user@example.com");
        loginPage.enterPassword("wrongpassword");
        loginPage.clickLoginExpectingError();

        assertThat(loginPage.isErrorDisplayed()).isTrue();
        assertThat(loginPage.getErrorMessage()).isEqualTo("Invalid email or password");
    }

    @Test(dataProvider = "invalidEmails", dataProviderClass = LoginDataProvider.class)
    public void testInvalidEmailFormats(String email, String expectedError) {
        loginPage.enterEmail(email);
        loginPage.enterPassword("SomePass123!");
        loginPage.clickLoginExpectingError();

        assertThat(loginPage.getErrorMessage()).contains(expectedError);
    }
}
```

### 数据提供者

```java
package com.example.dataproviders;

import org.testng.annotations.DataProvider;

public class LoginDataProvider {

    @DataProvider(name = "invalidEmails")
    public static Object[][] invalidEmails() {
        return new Object[][] {
            {"not-an-email", "Please enter a valid email"},
            {"@missing-local.com", "Please enter a valid email"},
            {"missing-at.com", "Please enter a valid email"},
            {"", "Email is required"},
        };
    }

    @DataProvider(name = "validCredentials")
    public static Object[][] validCredentials() {
        return new Object[][] {
            {"admin@example.com", "AdminPass123!", "Admin"},
            {"user@example.com", "UserPass123!", "User"},
        };
    }
}
```

## 显式等待 -- 模式

```java
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import java.time.Duration;

// 等待元素可点击
WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(10));
wait.until(ExpectedConditions.elementToBeClickable(By.id("submit"))).click();

// 等待元素可见
wait.until(ExpectedConditions.visibilityOfElementLocated(By.id("result")));

// 等待文本出现
wait.until(ExpectedConditions.textToBePresentInElementLocated(By.id("status"), "Complete"));

// 等待 URL 变化
wait.until(ExpectedConditions.urlContains("/dashboard"));

// 等待标题
wait.until(ExpectedConditions.titleContains("Dashboard"));

// 等待元素数量
wait.until(ExpectedConditions.numberOfElementsToBe(By.cssSelector(".item"), 5));

// 等待元素过期（从 DOM 中移除）
wait.until(ExpectedConditions.stalenessOf(oldElement));

// 自定义等待条件
wait.until(driver -> {
    String text = driver.findElement(By.id("counter")).getText();
    return Integer.parseInt(text) > 10;
});

// 带轮询的流畅等待
new FluentWait<>(driver)
    .withTimeout(Duration.ofSeconds(30))
    .pollingEvery(Duration.ofMillis(500))
    .ignoring(NoSuchElementException.class)
    .until(ExpectedConditions.visibilityOfElementLocated(By.id("result")));
```

## 处理常见场景

### 警报/对话框处理

```java
// 接受警报
driver.switchTo().alert().accept();

// 关闭警报
driver.switchTo().alert().dismiss();

// 获取警报文本
String alertText = driver.switchTo().alert().getText();

// 在提示框中输入
driver.switchTo().alert().sendKeys("input text");
```

### 框架处理

```java
// 通过索引切换
driver.switchTo().frame(0);

// 通过名称或 ID 切换
driver.switchTo().frame("frameName");

// 通过 WebElement 切换
WebElement iframe = driver.findElement(By.cssSelector("#payment-iframe"));
driver.switchTo().frame(iframe);

// 切换回主内容
driver.switchTo().defaultContent();
```

### 窗口/标签页处理

```java
String originalWindow = driver.getWindowHandle();

// 点击打开新标签页的链接
driver.findElement(By.id("new-tab-link")).click();

// 切换到新窗口
for (String handle : driver.getWindowHandles()) {
    if (!handle.equals(originalWindow)) {
        driver.switchTo().window(handle);
        break;
    }
}

// 在新窗口中执行操作
assertThat(driver.getTitle()).contains("New Page");

// 关闭并切换回来
driver.close();
driver.switchTo().window(originalWindow);
```

### Actions API

```java
import org.openqa.selenium.interactions.Actions;

Actions actions = new Actions(driver);

// 悬停
actions.moveToElement(element).perform();

// 双击
actions.doubleClick(element).perform();

// 右键点击
actions.contextClick(element).perform();

// 拖放
actions.dragAndDrop(source, target).perform();

// 键盘
actions.keyDown(Keys.CONTROL).click(element).keyUp(Keys.CONTROL).perform();
```

## TestNG XML 配置

```xml
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="Regression Suite" parallel="methods" thread-count="4">
    <listeners>
        <listener class-name="com.example.listeners.TestListener"/>
        <listener class-name="com.example.listeners.RetryListener"/>
    </listeners>

    <test name="Chrome Tests">
        <parameter name="browser" value="chrome"/>
        <classes>
            <class name="com.example.tests.LoginTest"/>
            <class name="com.example.tests.DashboardTest"/>
        </classes>
    </test>

    <test name="Firefox Tests">
        <parameter name="browser" value="firefox"/>
        <classes>
            <class name="com.example.tests.LoginTest"/>
        </classes>
    </test>
</suite>
```

## 最佳实践

1. **一致地使用页面对象模型** -- 每个页面交互都通过页面对象。
2. **优先使用显式等待** -- 绝不使用 `Thread.sleep()` 或隐式等待。
3. **使用 AssertJ 进行流畅断言** -- 比 TestNG 断言更具可读性。
4. **实现重试逻辑** -- 使用 TestNG `IRetryAnalyzer` 增强 flaky 测试弹性。
5. **集中管理驱动** -- 使用 `ThreadLocal` 确保并行安全。
6. **使用有意义的测试名称** -- 测试名称应描述场景。
7. **失败时捕获证据** -- 测试失败时截图并记录页面源码。
8. **外部化测试数据** -- 使用数据提供者、CSV 或 JSON 文件管理测试数据。
9. **使用相对 URL** -- 在属性中配置基础 URL 并动态构建 URL。
10. **记录操作** -- 在页面对象中使用 SLF4J 记录以便调试。

## 应避免的反模式

1. **`Thread.sleep()`** -- 始终使用带条件的显式等待。
2. **隐式等待** -- 它们与显式等待冲突并导致不可预测的行为。
3. **硬编码测试数据** -- 使用数据提供者或外部数据源。
4. **在测试中直接使用 `driver.findElement()`** -- 始终通过页面对象。
5. **不退出驱动** -- 内存泄漏和僵尸浏览器进程。
6. **使用绝对路径的 XPath** -- 使用相对 XPath 或 CSS 选择器。
7. **在线程间共享 WebDriver** -- 使用 ThreadLocal 进行并行执行。
8. **巨大的测试方法** -- 将复杂场景拆分为更小、更专注的测试。
9. **静默捕获异常** -- 让测试失败传播到框架。
10. **在 CI 中不使用无头模式** -- 无头执行在 CI 中更快更可靠。
