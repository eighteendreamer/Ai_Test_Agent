---
name: Espresso Android Testing
description: Android 原生 UI 测试框架，使用 Kotlin/Java 编写可维护的测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [mobile, e2e]
frameworks: [espresso]
languages: [kotlin, java]
info: vip.hctestedu.com
domains: [mobile]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Espresso Android 测试

您是一位专注于 Android UI 测试的 QA 工程师。当用户要求您编写、审查或调试 Espresso 测试时，请遵循这些详细说明。

## 核心原则

1. **稳定性优先** -- Espresso 的同步机制确保测试稳定。
2. **可读性** -- 测试应该像文档一样清晰。
3. **页面对象模式** -- 封装页面交互逻辑。
4. **单一职责** -- 每个测试验证一个行为。
5. **快速执行** -- 单元测试级别的执行速度。

## 项目结构

```
app/
├── src/
│   ├── main/
│   │   ├── java/com/example/
│   │   └── res/
│   ├── test/
│   │   └── java/com/example/
│   │       ├── unit/                    # 单元测试
│   │       └── androidTest/            # Espresso 测试
│   │           ├── flows/
│   │           │   └── LoginFlowTest.kt
│   │           ├── pages/
│   │           │   ├── LoginPage.kt
│   │           │   └── HomePage.kt
│   │           └── matchers/
│   │               └── CustomMatchers.kt
│   └── androidTest/
│       └── AndroidManifest.xml
├── build.gradle
└── app/build.gradle
```

## 配置

### Gradle 依赖

```kotlin
// app/build.gradle
android {
    defaultConfig {
        // 测试 instrumentation runner
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // 启用测试 APK 构建
    testBuildType = Debug
}

dependencies {
    // AndroidX Test
    androidTestImplementation("androidx.test:runner:1.5.2")
    androidTestImplementation("androidx.test:rules:1.5.0")
    androidTestImplementation("androidx.test:core-ktx:1.5.0")

    // Espresso
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.1")
    androidTestImplementation("androidx.test.espresso:espresso-intents:3.5.0")
    androidTestImplementation("androidx.test.espresso:espresso-web:3.5.1")

    // UI Automator（用于跨应用交互）
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.2.0")

    // JUnit 4
    androidTestImplementation("junit:junit:4.13.2")

    // Kotlin coroutines 测试
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
}
```

## 基础测试类

```kotlin
// src/androidTest/java/com/example/screens/BaseScreen.kt
package com.example.screens

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.espresso.Espresso
import androidx.test.espresso.ViewInteraction
import androidx.test.espresso.action.ViewActions
import androidx.test.espresso.assertion.ViewAssertions
import androidx.test.espresso.matcher.ViewMatchers
import androidx.test.platform.app.InstrumentationRegistry

abstract class BaseScreen {

    protected val context: Context
        get() = ApplicationProvider.getApplicationContext()

    protected val instrumentation = InstrumentationRegistry.getInstrumentation()

    // 点击操作
    protected fun click(element: ViewInteraction) {
        element.perform(ViewActions.click())
    }

    // 输入文本
    protected fun typeText(element: ViewInteraction, text: String) {
        element.perform(ViewActions.replaceText(text))
    }

    // 清除文本
    protected fun clearText(element: ViewInteraction) {
        element.perform(ViewActions.clearText())
    }

    // 按下返回键
    protected fun pressBack() {
        Espresso.pressBack()
    }

    // 验证元素可见
    protected fun assertVisible(element: ViewInteraction) {
        element.check(ViewAssertions.matches(ViewMatchers.isDisplayed()))
    }

    // 验证元素不存在
    protected fun assertNotVisible(element: ViewInteraction) {
        element.check(ViewAssertions.matches(ViewMatchers.isNotDisplayed()))
    }

    // 验证元素包含文本
    protected fun assertText(element: ViewInteraction, text: String) {
        element.check(ViewAssertions.matches(ViewMatchers.withText(text)))
    }
}
```

## 页面对象

### 登录页面

```kotlin
// src/androidTest/java/com/example/screens/LoginPage.kt
package com.example.screens

import androidx.test.espresso.ViewInteraction
import androidx.test.espresso.matcher.ViewMatchers
import com.example.R

class LoginPage : BaseScreen() {

    // 元素定位器
    private val emailInput: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.emailInput)
        )
    }

    private val passwordInput: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.passwordInput)
        )
    }

    private val loginButton: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.loginButton)
        )
    }

    private val forgotPasswordLink: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.forgotPasswordLink)
        )
    }

    private val errorMessage: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.errorMessage)
        )
    }

    private val loadingIndicator: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.loadingIndicator)
        )
    }

    // 操作方法
    fun enterEmail(email: String): LoginPage {
        typeText(emailInput, email)
        return this
    }

    fun enterPassword(password: String): LoginPage {
        typeText(passwordInput, password)
        return this
    }

    fun clearEmail(): LoginPage {
        clearText(emailInput)
        return this
    }

    fun clearPassword(): LoginPage {
        clearText(passwordInput)
        return this
    }

    fun clickLogin(): HomePage {
        click(loginButton)
        // 等待导航完成
        instrumentation.waitForIdleSync()
        return HomePage()
    }

    fun clickLoginExpectingError(): LoginPage {
        click(loginButton)
        instrumentation.waitForIdleSync()
        return this
    }

    fun clickForgotPassword(): ForgotPasswordPage {
        click(forgotPasswordLink)
        instrumentation.waitForIdleSync()
        return ForgotPasswordPage()
    }

    // 断言方法
    fun assertEmailErrorVisible(): LoginPage {
        assertVisible(errorMessage)
        return this
    }

    fun assertEmailErrorTextContains(expectedText: String): LoginPage {
        errorMessage.check(
            ViewAssertions.matches(
                ViewMatchers.withText(
                    ViewMatchers.stringContainsIn(expectedText)
                )
            )
        )
        return this
    }

    fun assertLoadingIndicatorVisible(): LoginPage {
        assertVisible(loadingIndicator)
        return this
    }

    fun assertLoginButtonEnabled(): LoginPage {
        loginButton.check(
            ViewAssertions.matches(ViewMatchers.isEnabled())
        )
        return this
    }

    fun assertLoginButtonDisabled(): LoginPage {
        loginButton.check(
            ViewAssertions.matches(ViewMatchers.isNotEnabled())
        )
        return this
    }

    // 组合操作
    fun login(email: String, password: String): HomePage {
        return enterEmail(email)
            .enterPassword(password)
            .clickLogin()
    }
}
```

### 主页页面

```kotlin
// src/androidTest/java/com/example/screens/HomePage.kt
package com.example.screens

import androidx.test.espresso.ViewInteraction
import androidx.test.espresso.matcher.ViewMatchers
import com.example.R

class HomePage : BaseScreen() {

    private val welcomeMessage: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.welcomeMessage)
        )
    }

    private val userProfile: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.userProfile)
        )
    }

    private val logoutButton: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.logoutButton)
        )
    }

    private val settingsButton: ViewInteraction by lazy {
        ViewMatchers.onView(
            ViewMatchers.withId(R.id.settingsButton)
        )
    }

    fun assertWelcomeMessageVisible(): HomePage {
        assertVisible(welcomeMessage)
        return this
    }

    fun assertWelcomeMessageContains(userName: String): HomePage {
        welcomeMessage.check(
            ViewAssertions.matches(
                ViewMatchers.withText(
                    ViewMatchers.stringContainsIn(userName)
                )
            )
        )
        return this
    }

    fun clickProfile(): ProfilePage {
        click(userProfile)
        instrumentation.waitForIdleSync()
        return ProfilePage()
    }

    fun clickSettings(): SettingsPage {
        click(settingsButton)
        instrumentation.waitForIdleSync()
        return SettingsPage()
    }

    fun logout(): LoginPage {
        click(logoutButton)
        instrumentation.waitForIdleSync()
        return LoginPage()
    }
}
```

## 测试用例

### 登录流程测试

```kotlin
// src/androidTest/java/com/example/flows/LoginFlowTest.kt
package com.example.flows

import androidx.test.ext.junit.rules.activityScenarioRule
import androidx.test.filters.LargeTest
import com.example.HomeActivity
import com.example.screens.LoginPage
import org.junit.Rule
import org.junit.Test

@LargeTest
class LoginFlowTest {

    @get:Rule
    val activityScenarioRule = activityScenarioRule<HomeActivity>()

    private val loginPage = LoginPage()

    @Test
    fun login_withValidCredentials_shouldNavigateToHome() {
        loginPage
            .enterEmail("test@example.com")
            .enterPassword("SecurePass123!")
            .clickLogin()
            .assertWelcomeMessageVisible()
    }

    @Test
    fun login_withInvalidCredentials_shouldShowError() {
        loginPage
            .enterEmail("invalid@example.com")
            .enterPassword("wrongpassword")
            .clickLoginExpectingError()
            .assertEmailErrorVisible()
            .assertEmailErrorTextContains("Invalid credentials")
    }

    @Test
    fun login_withEmptyFields_shouldShowValidationError() {
        loginPage
            .assertLoginButtonDisabled()
            .enterEmail("test@example.com")
            .assertLoginButtonDisabled()
            .enterPassword("password")
            .assertLoginButtonEnabled()
    }

    @Test
    fun login_withInvalidEmail_shouldShowEmailFormatError() {
        loginPage
            .enterEmail("not-an-email")
            .enterPassword("password")
            .clickLoginExpectingError()
            .assertEmailErrorTextContains("valid email")
    }

    @Test
    fun login_withEmptyEmail_shouldShowRequiredError() {
        loginPage
            .enterPassword("password")
            .clickLoginExpectingError()
            .assertEmailErrorTextContains("required")
    }
}
```

### 数据驱动测试

```kotlin
// src/androidTest/java/com/example/flows/LoginDataDrivenTest.kt
package com.example.flows

import androidx.test.ext.junit.rules.activityScenarioRule
import androidx.test.filters.LargeTest
import com.example.HomeActivity
import com.example.screens.LoginPage
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

@RunWith(Parameterized::class)
class LoginDataDrivenTest(
    private val email: String,
    private val password: String,
    private val shouldSucceed: Boolean
) {

    companion object {
        @JvmStatic
        @Parameterized.Parameters(name = "email={0}, password={1}, shouldSucceed={2}")
        fun data(): List<Array<Any>> = listOf(
            arrayOf("test@example.com", "SecurePass123!", true),
            arrayOf("user@example.com", "UserPass123!", true),
            arrayOf("invalid@example.com", "wrongpassword", false),
            arrayOf("test@example.com", "", false),
            arrayOf("", "password", false),
            arrayOf("not-an-email", "password", false),
        )
    }

    @get:Rule
    val activityScenarioRule = activityScenarioRule<HomeActivity>()

    private val loginPage = LoginPage()

    @Test
    fun login_withVariousCredentials() {
        if (shouldSucceed) {
            loginPage
                .enterEmail(email)
                .enterPassword(password)
                .clickLogin()
                .assertWelcomeMessageVisible()
        } else {
            loginPage
                .enterEmail(email)
                .enterPassword(password)
                .clickLoginExpectingError()
                .assertEmailErrorVisible()
        }
    }
}
```

## 自定义匹配器

```kotlin
// src/androidTest/java/com/example/matchers/CustomMatchers.kt
package com.example.matchers

import android.view.View
import androidx.test.espresso.matcher.BoundedMatcher
import org.hamcrest.Description
import org.hamcrest.Matcher

object CustomMatchers {

    fun withBackgroundColor(color: Int): Matcher<View> {
        return object : BoundedMatcher<View, View>(View::class.java) {
            override fun describeTo(description: Description) {
                description.appendText("with background color: $color")
            }

            override fun matchesSafely(item: View): Boolean {
                return item.backgroundTintList?.defaultColor == color
            }
        }
    }

    fun withAlpha(alpha: Float): Matcher<View> {
        return object : BoundedMatcher<View, View>(View::class.java) {
            override fun describeTo(description: Description) {
                description.appendText("with alpha: $alpha")
            }

            override fun matchesSafely(item: View): Boolean {
                return item.alpha == alpha
            }
        }
    }

    fun isFirstChildOf(parentMatcher: Matcher<View>): Matcher<View> {
        return object : BoundedMatcher<View, View>(View::class.java) {
            override fun describeTo(description: Description) {
                description.appendText("is first child of $parentMatcher")
            }

            override fun matchesSafely(view: View): Boolean {
                val parent = view.parent as? View ?: return false
                if (!parentMatcher.matches(parent)) return false
                return parent.indexOfChild(view) == 0
            }
        }
    }

    fun withEffectiveVisibility(visibility: ViewMatchers.Visibility): Matcher<View> {
        return object : BoundedMatcher<View, View>(View::class.java) {
            override fun describeTo(description: Description) {
                description.appendText("with effective visibility: $visibility")
            }

            override fun matchesSafely(item: View): Boolean {
                return ViewMatchers.withEffectiveVisibility(item).matches(visibility)
            }
        }
    }
}
```

## Intent 测试

```kotlin
// src/androidTest/java/com/example/flows/ExternalIntentTest.kt
package com.example.flows

import android.content.Intent
import androidx.test.espresso.intent.Intents
import androidx.test.espresso.intent.rule.IntentsRule
import androidx.test.ext.junit.rules.activityScenarioRule
import androidx.test.filters.LargeTest
import com.example.HomeActivity
import com.example.screens.HomePage
import com.example.screens.LoginPage
import org.junit.Rule
import org.junit.Test

@LargeTest
class ExternalIntentTest {

    @get:Rule
    val activityScenarioRule = activityScenarioRule<HomeActivity>()

    @get:Rule
    val intentsRule = IntentsRule()

    private val homePage = HomePage()

    @Test
    fun clickShare_shouldOpenShareIntent() {
        homePage.clickShare()

        Intents.intended(Intent.hasAction(Intent.ACTION_SENDTO))
    }

    @Test
    fun clickEmail_shouldOpenEmailClient() {
        homePage.clickEmailLink()

        Intents.intended(
            Intent.hasData("mailto:")
        )
    }

    @Test
    fun clickBrowser_shouldOpenExternalBrowser() {
        homePage.clickExternalLink()

        Intents.intended(
            Intent.hasAction(Intent.ACTION_VIEW)
        )
    }
}
```

## CI/CD 集成

```yaml
# .github/workflows/android-tests.yml
name: Android Espresso Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  espresso-test:
    runs-on: macos-latest

    steps:
      - uses: actions/checkout@v4

      - name: Setup JDK
        uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'

      - name: Setup Android SDK
        uses: android-actions/setup-android@v2

      - name: Cache Gradle packages
        uses: actions/cache@v4
        with:
          path: |
            ~/.gradle/caches
            ~/.gradle/wrapper
          key: ${{ runner.os }}-gradle-${{ hashFiles('**/*.gradle*', '**/gradle-wrapper.properties') }}
          restore-keys: |
            ${{ runner.os }}-gradle-

      - name: Build debug APK
        run: ./gradlew assembleDebug

      - name: Build test APK
        run: ./gradlew assembleDebugAndroidTest

      - name: Run Espresso tests
        uses: reactivecircus/android-emulator-runner@v2
        with:
          api-level: 33
          target: google_apis
          arch: x86_64
          profile: Nexus 6
          script: |
            ./gradlew connectedDebugAndroidTest

      - name: Upload test results
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: espresso-test-results
          path: app/build/reports/androidTests/connected

      - name: Upload screenshots
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: espresso-screenshots
          path: app/build/reports/androidTests/connected/*/images
```

## 最佳实践

1. **使用 ID 而非文本** -- testId 比文本更稳定。
2. **页面对象模式** -- 封装页面交互逻辑。
3. **显式等待** -- Espresso 自动同步，但仍需合理等待。
4. **数据驱动测试** -- 使用 Parameterized 测试多种输入。
5. **独立的测试** -- 每个测试应该独立运行。
6. **清晰的断言** -- 使用有意义的断言消息。
7. **适当的测试粒度** -- 不要一个测试做太多事情。
8. **截图失败记录** -- 便于调试失败的测试。

## 应避免的反模式

1. **硬编码 sleep** -- 使用 Espresso 的同步机制。
2. **复杂的 XPath** -- 使用 ID 或文本匹配器。
3. **过长的测试** -- 拆分成多个小测试。
4. **测试实现细节** -- 应该测试用户可见的行为。
5. **忽略测试隔离** -- 测试之间不应该有依赖。
6. **不使用 testId** -- 添加 contentDescription 或 testId。
7. **过度模拟** -- 使用真实组件进行集成测试。
8. **忽略性能** -- 测试应该快速执行。