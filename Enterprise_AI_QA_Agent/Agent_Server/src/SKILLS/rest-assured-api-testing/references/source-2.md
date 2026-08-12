---
name: REST Assured API Automation Framework
description: 生产级 REST API 自动化框架,包含 REST Assured、使用 GSON 的 POJO 序列化、PayloadManager 模式、使用 TestNG ITestContext 的 E2E 集成工作流和 Allure 报告。
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [api, integration]
frameworks: [rest-assured]
languages: [java]
info: vip.hctestedu.com
domains: [api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
githubUrl: https://github.com/PramodDutta/APIAutomationFramworkATB11x
---

# REST Assured API 自动化框架技能

你是一位专注于使用 Java 和 REST Assured 进行 REST API 自动化的 QA 自动化专家。当用户要求你构建、审查或调试 API 测试自动化框架时,请遵循以下详细说明,涵盖基于 POJO 的请求/响应处理、PayloadManager 模式、E2E 集成工作流和高级报告。

## 核心原则

1. **PayloadManager 模式** -- 使用 GSON 在专门的 PayloadManager 类中集中所有请求载荷创建和响应反序列化。
2. **POJO 驱动的请求和响应** -- 使用带有 `@SerializedName` 和 `@Expose` 注解的 Java 对象进行类型安全的 JSON 处理。
3. **RequestSpecBuilder 实现 DRY 设置** -- 在 BaseTest 中配置一次 base URI、头和内容类型,在所有测试中重用。
4. **自定义断言辅助** -- 使用 AssertJ 在 AssertActions 类中包装常见断言以实现流畅、可读的验证。
5. **使用 ITestContext 的 E2E 集成** -- 使用 TestNG 的 ITestContext 跨测试方法共享状态(预订 ID、令牌)以实现多步骤工作流。
6. **集中式 API 常量** -- 将所有端点路径存储在单个 APIConstants 类中,永远不要在测试中硬编码 URL。
7. **多种数据策略** -- 支持静态载荷、JavaFaker 随机数据和边界情况载荷以实现全面覆盖。
8. **每个测试上的 Allure 元数据** -- 使用 @Description、@Owner、@TmsLink 注解以在报告中实现完全可追溯性。

## 项目结构

```
src/
  main/java/com/thetestingacademy/
    endpoints/
      APIConstants.java               # Base URL and endpoint paths
    modules/
      PayloadManager.java             # Payload creation and response parsing
    pojos/
      request/
        Auth.java                     # Authentication POJO
        Booking.java                  # Booking request POJO
        Bookingdates.java             # Nested dates POJO
      reponse/
        BookingResponse.java          # Booking response POJO
        TokenResponse.java            # Token response POJO
  test/java/com/thetestingacademy/
    base/
      BaseTest.java                   # Setup, teardown, token helper
    asserts/
      AssertActions.java              # Custom assertion methods
    tests/
      crud/
        TestHealthCheck.java          # API health check
        TestCreateToken.java          # Token creation test
        TestCreateBooking.java        # CRUD booking tests
      e2e_integration/
        TestIntegrationFlow1.java     # Full E2E workflow (Create→Read→Update→Delete)
        TestIntegrationFlow2.java     # Alternate integration flow
      sample/
        TestIntegrationSample.java    # Test template
    resources/
      data.properties                 # Configuration
testng.xml                            # Default test suite
testng_e2e.xml                        # E2E integration suite
pom.xml
```

## Maven 依赖

```xml
<dependencies>
    <dependency>
        <groupId>io.rest-assured</groupId>
        <artifactId>rest-assured</artifactId>
        <version>5.5.1</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>io.rest-assured</groupId>
        <artifactId>json-schema-validator</artifactId>
        <version>5.4.0</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.testng</groupId>
        <artifactId>testng</artifactId>
        <version>7.10.2</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>io.qameta.allure</groupId>
        <artifactId>allure-testng</artifactId>
        <version>2.27.0</version>
    </dependency>
    <dependency>
        <groupId>com.google.code.gson</groupId>
        <artifactId>gson</artifactId>
        <version>2.11.0</version>
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
        <version>5.2.3</version>
    </dependency>
    <dependency>
        <groupId>com.github.javafaker</groupId>
        <artifactId>javafaker</artifactId>
        <version>1.0.2</version>
    </dependency>
    <dependency>
        <groupId>org.apache.logging.log4j</groupId>
        <artifactId>log4j-core</artifactId>
        <version>2.24.0</version>
    </dependency>
</dependencies>

<build>
    <plugins>
        <plugin>
            <groupId>org.apache.maven.plugins</groupId>
            <artifactId>maven-surefire-plugin</artifactId>
            <version>3.1.2</version>
            <configuration>
                <suiteXmlFiles>
                    <suiteXmlFile>testng.xml</suiteXmlFile>
                </suiteXmlFiles>
            </configuration>
        </plugin>
    </plugins>
</build>
```

## API 常量

```java
package com.thetestingacademy.endpoints;

public class APIConstants {
    public static String BASE_URL = "https://restful-booker.herokuapp.com";
    public static String CREATE_UPDATE_BOOKING_URL = "/booking";
    public static String AUTH_URL = "/auth";
    public static String PING_URL = "/ping";
}
```

## POJO 模型 -- 请求

### Auth.java

```java
package com.thetestingacademy.pojos.request;

import com.google.gson.annotations.Expose;
import com.google.gson.annotations.SerializedName;

public class Auth {
    @SerializedName("username")
    @Expose
    private String username;

    @SerializedName("password")
    @Expose
    private String password;

    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }
}
```

### Booking.java

```java
package com.thetestingacademy.pojos.request;

import com.google.gson.annotations.Expose;
import com.google.gson.annotations.SerializedName;

public class Booking {
    @SerializedName("firstname")
    @Expose
    private String firstname;

    @SerializedName("lastname")
    @Expose
    private String lastname;

    @SerializedName("totalprice")
    @Expose
    private Integer totalprice;

    @SerializedName("depositpaid")
    @Expose
    private Boolean depositpaid;

    @SerializedName("bookingdates")
    @Expose
    private Bookingdates bookingdates;

    @SerializedName("additionalneeds")
    @Expose
    private String additionalneeds;

    // Getters and Setters
    public String getFirstname() { return firstname; }
    public void setFirstname(String firstname) { this.firstname = firstname; }
    public String getLastname() { return lastname; }
    public void setLastname(String lastname) { this.lastname = lastname; }
    public Integer getTotalprice() { return totalprice; }
    public void setTotalprice(Integer totalprice) { this.totalprice = totalprice; }
    public Boolean getDepositpaid() { return depositpaid; }
    public void setDepositpaid(Boolean depositpaid) { this.depositpaid = depositpaid; }
    public Bookingdates getBookingdates() { return bookingdates; }
    public void setBookingdates(Bookingdates bookingdates) { this.bookingdates = bookingdates; }
    public String getAdditionalneeds() { return additionalneeds; }
    public void setAdditionalneeds(String additionalneeds) { this.additionalneeds = additionalneeds; }
}
```

### Bookingdates.java

```java
package com.thetestingacademy.pojos.request;

import com.google.gson.annotations.Expose;
import com.google.gson.annotations.SerializedName;

public class Bookingdates {
    @SerializedName("checkin")
    @Expose
    private String checkin;

    @SerializedName("checkout")
    @Expose
    private String checkout;

    public String getCheckin() { return checkin; }
    public void setCheckin(String checkin) { this.checkin = checkin; }
    public String getCheckout() { return checkout; }
    public void setCheckout(String checkout) { this.checkout = checkout; }
}
```

## POJO 模型 -- 响应

### TokenResponse.java

```java
package com.thetestingacademy.pojos.reponse;

import com.google.gson.annotations.Expose;
import com.google.gson.annotations.SerializedName;

public class TokenResponse {
    @SerializedName("token")
    @Expose
    private String token;

    public String getToken() { return token; }
    public void setToken(String token) { this.token = token; }
}
```

### BookingResponse.java

```java
package com.thetestingacademy.pojos.reponse;

import com.google.gson.annotations.Expose;
import com.google.gson.annotations.SerializedName;
import com.thetestingacademy.pojos.request.Booking;

public class BookingResponse {
    @SerializedName("bookingid")
    @Expose
    private Integer bookingid;

    @SerializedName("booking")
    @Expose
    private Booking booking;

    public Integer getBookingid() { return bookingid; }
    public void setBookingid(Integer bookingid) { this.bookingid = bookingid; }
    public Booking getBooking() { return booking; }
    public void setBooking(Booking booking) { this.booking = booking; }
}
```

## PayloadManager 模式

PayloadManager 集中所有载荷创建、序列化和反序列化。这保持测试类干净并专注于断言。

```java
package com.thetestingacademy.modules;

import com.github.javafaker.Faker;
import com.google.gson.Gson;
import com.thetestingacademy.pojos.reponse.BookingResponse;
import com.thetestingacademy.pojos.reponse.TokenResponse;
import com.thetestingacademy.pojos.request.Auth;
import com.thetestingacademy.pojos.request.Booking;
import com.thetestingacademy.pojos.request.Bookingdates;

public class PayloadManager {
    Gson gson;
    Faker faker;

    // --- Serialization: Java Object → JSON String ---

    public String createPayloadBookingAsString() {
        Booking booking = new Booking();
        booking.setFirstname("Pramod");
        booking.setLastname("Dutta");
        booking.setTotalprice(112);
        booking.setDepositpaid(true);

        Bookingdates bookingdates = new Bookingdates();
        bookingdates.setCheckin("2024-02-01");
        bookingdates.setCheckout("2024-02-10");
        booking.setBookingdates(bookingdates);
        booking.setAdditionalneeds("Breakfast");

        gson = new Gson();
        return gson.toJson(booking);
    }

    // Payload with random data using JavaFaker
    public String createPayloadBookingFakerJS() {
        faker = new Faker();
        Booking booking = new Booking();
        booking.setFirstname(faker.name().firstName());
        booking.setLastname(faker.name().lastName());
        booking.setTotalprice(faker.random().nextInt(1, 1000));
        booking.setDepositpaid(faker.random().nextBoolean());

        Bookingdates bookingdates = new Bookingdates();
        bookingdates.setCheckin("2024-02-01");
        bookingdates.setCheckout("2024-02-10");
        booking.setBookingdates(bookingdates);
        booking.setAdditionalneeds("Lunch");

        gson = new Gson();
        return gson.toJson(booking);
    }

    // Edge case payload with non-ASCII characters
    public String createPayloadBookingAsStringWrongBody() {
        Booking booking = new Booking();
        booking.setFirstname("会意; 會意");
        booking.setLastname("Test");
        booking.setTotalprice(112);
        booking.setDepositpaid(true);

        Bookingdates bookingdates = new Bookingdates();
        bookingdates.setCheckin("5025-02-01");
        bookingdates.setCheckout("5025-02-10");
        booking.setBookingdates(bookingdates);
        booking.setAdditionalneeds("Breakfast");

        gson = new Gson();
        return gson.toJson(booking);
    }

    // Full update payload
    public String fullUpdatePayloadAsString() {
        Booking booking = new Booking();
        booking.setFirstname("UpdatedFirstName");
        booking.setLastname("UpdatedLastName");
        booking.setTotalprice(500);
        booking.setDepositpaid(false);

        Bookingdates bookingdates = new Bookingdates();
        bookingdates.setCheckin("2024-03-01");
        bookingdates.setCheckout("2024-03-15");
        booking.setBookingdates(bookingdates);
        booking.setAdditionalneeds("Dinner");

        gson = new Gson();
        return gson.toJson(booking);
    }

    // Auth payload
    public String setAuthPayload() {
        Auth auth = new Auth();
        auth.setUsername("admin");
        auth.setPassword("password123");
        gson = new Gson();
        return gson.toJson(auth);
    }

    // --- Deserialization: JSON String → Java Object ---

    public BookingResponse bookingResponseJava(String responseString) {
        gson = new Gson();
        return gson.fromJson(responseString, BookingResponse.class);
    }

    public String getTokenFromJSON(String tokenResponse) {
        gson = new Gson();
        TokenResponse response = gson.fromJson(tokenResponse, TokenResponse.class);
        return response.getToken();
    }
}
```

## Base Test 类

```java
package com.thetestingacademy.base;

import com.thetestingacademy.asserts.AssertActions;
import com.thetestingacademy.endpoints.APIConstants;
import com.thetestingacademy.modules.PayloadManager;
import io.restassured.RestAssured;
import io.restassured.builder.RequestSpecBuilder;
import io.restassured.http.ContentType;
import io.restassured.path.json.JsonPath;
import io.restassured.response.Response;
import io.restassured.response.ValidatableResponse;
import io.restassured.specification.RequestSpecification;
import org.testng.annotations.BeforeTest;
import org.testng.annotations.AfterTest;

public class BaseTest {
    public RequestSpecification requestSpecification;
    public AssertActions assertActions;
    public PayloadManager payloadManager;
    public JsonPath jsonPath;
    public Response response;
    public ValidatableResponse validatableResponse;

    @BeforeTest
    public void setUp() {
        payloadManager = new PayloadManager();
        assertActions = new AssertActions();

        requestSpecification = new RequestSpecBuilder()
            .setBaseUri(APIConstants.BASE_URL)
            .addHeader("Content-Type", "application/json")
            .build().log().all();
    }

    public String getToken() {
        requestSpecification = RestAssured.given();
        requestSpecification.baseUri(APIConstants.BASE_URL)
            .basePath(APIConstants.AUTH_URL);

        String payload = payloadManager.setAuthPayload();
        response = requestSpecification.contentType(ContentType.JSON)
            .body(payload).when().post();

        return payloadManager.getTokenFromJSON(response.asString());
    }

    @AfterTest
    public void tearDown() {
        System.out.println("Finished the Test!");
    }
}
```

## 自定义断言辅助

```java
package com.thetestingacademy.asserts;

import io.restassured.response.Response;

import static org.assertj.core.api.Assertions.assertThat;
import static org.testng.Assert.assertEquals;

public class AssertActions {

    public void verifyResponseBody(String actual, String expected, String description) {
        assertEquals(actual, expected, description);
    }

    public void verifyResponseBody(int actual, int expected, String description) {
        assertEquals(actual, expected, description);
    }

    public void verifyStatusCode(Response response, Integer expected) {
        assertEquals(response.getStatusCode(), (int) expected);
    }

    public void verifyStringKey(String keyExpect, String keyActual) {
        assertThat(keyExpect).isNotNull();
        assertThat(keyExpect).isNotBlank();
        assertThat(keyExpect).isEqualTo(keyActual);
    }

    public void verifyStringKeyNotNull(Integer keyExpect) {
        assertThat(keyExpect).isNotNull();
    }

    public void verifyStringKeyNotNull(String keyExpect) {
        assertThat(keyExpect).isNotNull();
    }

    public void verifyResponseTime(Response response, long maxMillis) {
        assertThat(response.getTime()).isLessThan(maxMillis);
    }

    public void verifyContentType(Response response, String expectedContentType) {
        assertThat(response.getContentType()).contains(expectedContentType);
    }
}
```

## CRUD 测试模式

### 健康检查

```java
package com.thetestingacademy.tests.crud;

import com.thetestingacademy.base.BaseTest;
import com.thetestingacademy.endpoints.APIConstants;
import io.restassured.RestAssured;
import org.testng.annotations.Test;

public class TestHealthCheck extends BaseTest {

    @Test(groups = "reg", priority = 1)
    public void testHealthCheckGET() {
        requestSpecification.basePath(APIConstants.PING_URL);
        response = RestAssured.given(requestSpecification)
            .when()
            .get();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(201);
    }
}
```

### 令牌创建

```java
package com.thetestingacademy.tests.crud;

import com.thetestingacademy.base.BaseTest;
import com.thetestingacademy.endpoints.APIConstants;
import io.qameta.allure.Description;
import io.qameta.allure.Owner;
import io.restassured.RestAssured;
import org.testng.annotations.Test;

public class TestCreateToken extends BaseTest {

    @Test(groups = "reg", priority = 1)
    @Owner("Promode")
    @Description("TC#2 - Create Token and Verify")
    public void testTokenPOST() {
        requestSpecification.basePath(APIConstants.AUTH_URL);
        response = RestAssured.given(requestSpecification)
            .when()
            .body(payloadManager.setAuthPayload())
            .post();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);

        String token = payloadManager.getTokenFromJSON(response.asString());
        assertActions.verifyStringKeyNotNull(token);
    }
}
```

### 预订 CRUD -- 正向和负向

```java
package com.thetestingacademy.tests.crud;

import com.thetestingacademy.base.BaseTest;
import com.thetestingacademy.endpoints.APIConstants;
import com.thetestingacademy.pojos.reponse.BookingResponse;
import io.qameta.allure.Description;
import io.qameta.allure.Owner;
import io.restassured.RestAssured;
import org.testng.annotations.Test;

public class TestCreateBooking extends BaseTest {

    @Test(groups = "reg", priority = 1)
    @Owner("Promode")
    @Description("TC#1 - Verify that the Booking can be Created")
    public void testCreateBookingPOST_Positive() {
        requestSpecification.basePath(APIConstants.CREATE_UPDATE_BOOKING_URL);
        response = RestAssured.given(requestSpecification)
            .when()
            .body(payloadManager.createPayloadBookingAsString())
            .log().all()
            .post();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);

        BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());

        assertActions.verifyStringKeyNotNull(bookingResponse.getBookingid());
        assertActions.verifyStringKey(bookingResponse.getBooking().getFirstname(), "Pramod");
    }

    @Test(groups = "reg", priority = 2)
    @Description("TC#2 - Verify booking with empty payload returns error")
    public void testCreateBookingPOST_Negative_EmptyBody() {
        requestSpecification.basePath(APIConstants.CREATE_UPDATE_BOOKING_URL);
        response = RestAssured.given(requestSpecification)
            .when()
            .body("")
            .post();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(500);
    }

    @Test(groups = "reg", priority = 3)
    @Description("TC#3 - Verify booking with non-ASCII characters")
    public void testCreateBookingPOST_EdgeCase_NonASCII() {
        requestSpecification.basePath(APIConstants.CREATE_UPDATE_BOOKING_URL);
        response = RestAssured.given(requestSpecification)
            .when()
            .body(payloadManager.createPayloadBookingAsStringWrongBody())
            .post();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);

        BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());
        assertActions.verifyStringKeyNotNull(bookingResponse.getBookingid());
    }

    @Test(groups = "qa", priority = 4)
    @Description("TC#4 - Verify booking with random Faker data")
    public void testCreateBookingPOST_FakerData() {
        requestSpecification.basePath(APIConstants.CREATE_UPDATE_BOOKING_URL);
        response = RestAssured.given(requestSpecification)
            .when()
            .body(payloadManager.createPayloadBookingFakerJS())
            .post();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);

        BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());
        assertActions.verifyStringKeyNotNull(bookingResponse.getBookingid());
        assertActions.verifyStringKeyNotNull(bookingResponse.getBooking().getFirstname());
    }
}
```

## 使用 ITestContext 的 E2E 集成流

此模式演示了完整的 CRUD 工作流,其中测试方法通过 TestNG 的 `ITestContext` 共享状态。每个步骤依赖于前一个步骤。

```java
package com.thetestingacademy.tests.e2e_integration;

import com.thetestingacademy.base.BaseTest;
import com.thetestingacademy.endpoints.APIConstants;
import com.thetestingacademy.pojos.reponse.BookingResponse;
import io.restassured.RestAssured;
import org.testng.ITestContext;
import org.testng.annotations.Test;

public class TestIntegrationFlow1 extends BaseTest {

    // Step 1: Create a booking
    @Test(priority = 1)
    public void testCreateBooking(ITestContext iTestContext) {
        requestSpecification.basePath(APIConstants.CREATE_UPDATE_BOOKING_URL);
        response = RestAssured.given(requestSpecification)
            .when()
            .body(payloadManager.createPayloadBookingAsString())
            .post();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);

        BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());

        // Store booking ID for subsequent steps
        iTestContext.setAttribute("bookingid", bookingResponse.getBookingid());

        assertActions.verifyStringKeyNotNull(bookingResponse.getBookingid());
    }

    // Step 2: Verify the booking exists
    @Test(priority = 2)
    public void testVerifyBookingId(ITestContext iTestContext) {
        Integer bookingid = (Integer) iTestContext.getAttribute("bookingid");

        String basePathGET = APIConstants.CREATE_UPDATE_BOOKING_URL + "/" + bookingid;
        requestSpecification.basePath(basePathGET);

        response = RestAssured.given(requestSpecification)
            .when()
            .get();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);

        // Verify the returned booking matches what we created
        BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());
        assertActions.verifyStringKey(bookingResponse.getBooking().getFirstname(), "Pramod");
    }

    // Step 3: Update the booking (requires auth token)
    @Test(priority = 3)
    public void testUpdateBookingByID(ITestContext iTestContext) {
        Integer bookingid = (Integer) iTestContext.getAttribute("bookingid");
        String token = getToken();

        // Store token for the delete step
        iTestContext.setAttribute("token", token);

        String basePathPUT = APIConstants.CREATE_UPDATE_BOOKING_URL + "/" + bookingid;
        requestSpecification.basePath(basePathPUT);

        response = RestAssured.given(requestSpecification)
            .cookie("token", token)
            .when()
            .body(payloadManager.fullUpdatePayloadAsString())
            .put();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);
    }

    // Step 4: Verify the update
    @Test(priority = 4)
    public void testVerifyUpdatedBooking(ITestContext iTestContext) {
        Integer bookingid = (Integer) iTestContext.getAttribute("bookingid");

        String basePathGET = APIConstants.CREATE_UPDATE_BOOKING_URL + "/" + bookingid;
        requestSpecification.basePath(basePathGET);

        response = RestAssured.given(requestSpecification)
            .when()
            .get();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(200);

        BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());
        assertActions.verifyStringKey(bookingResponse.getBooking().getFirstname(), "UpdatedFirstName");
    }

    // Step 5: Delete the booking
    @Test(priority = 5)
    public void testDeleteBookingById(ITestContext iTestContext) {
        Integer bookingid = (Integer) iTestContext.getAttribute("bookingid");
        String token = (String) iTestContext.getAttribute("token");

        String basePathDELETE = APIConstants.CREATE_UPDATE_BOOKING_URL + "/" + bookingid;
        requestSpecification.basePath(basePathDELETE);

        response = RestAssured.given().spec(requestSpecification)
            .cookie("token", token)
            .when()
            .delete();

        validatableResponse = response.then().log().all();
        validatableResponse.statusCode(201);
    }
}
```

## 认证处理

```java
// Token-based authentication flow:

// 1. Generate token via /auth endpoint
public String getToken() {
    requestSpecification = RestAssured.given();
    requestSpecification.baseUri(APIConstants.BASE_URL)
        .basePath(APIConstants.AUTH_URL);

    String payload = payloadManager.setAuthPayload();
    response = requestSpecification.contentType(ContentType.JSON)
        .body(payload).when().post();

    return payloadManager.getTokenFromJSON(response.asString());
}

// 2. Use token as cookie in protected requests
String token = getToken();
response = RestAssured.given(requestSpecification)
    .cookie("token", token)
    .when()
    .body(payloadManager.fullUpdatePayloadAsString())
    .put();

// 3. Bearer token alternative
response = RestAssured.given(requestSpecification)
    .header("Authorization", "Bearer " + token)
    .when()
    .get();
```

## TestNG XML 配置

### 默认套件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="API Test Suite">
    <test verbose="2" preserve-order="true" name="CRUD Tests">
        <classes>
            <class name="com.thetestingacademy.tests.crud.TestHealthCheck"/>
            <class name="com.thetestingacademy.tests.crud.TestCreateToken"/>
            <class name="com.thetestingacademy.tests.crud.TestCreateBooking"/>
        </classes>
    </test>
</suite>
```

### E2E 集成套件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="E2E Integration Suite">
    <test verbose="2" preserve-order="true" name="Integration Flow 1">
        <classes>
            <class name="com.thetestingacademy.tests.e2e_integration.TestIntegrationFlow1"/>
        </classes>
    </test>
</suite>
```

### 并行执行套件

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "https://testng.org/testng-1.0.dtd">
<suite name="Parallel API Suite" parallel="methods" thread-count="3">
    <test verbose="2" name="Regression">
        <groups>
            <run>
                <include name="reg"/>
            </run>
        </groups>
        <classes>
            <class name="com.thetestingacademy.tests.crud.TestHealthCheck"/>
            <class name="com.thetestingacademy.tests.crud.TestCreateToken"/>
            <class name="com.thetestingacademy.tests.crud.TestCreateBooking"/>
        </classes>
    </test>
</suite>
```

## Allure 报告

### 注解

```java
import io.qameta.allure.*;

@Test(groups = "reg", priority = 1)
@TmsLink("https://bugz.atlassian.net/browse/TS-1")
@Owner("Promode")
@Description("TC#1 - Verify that the Booking can be Created")
@Severity(SeverityLevel.CRITICAL)
@Story("Booking CRUD")
@Feature("Booking API")
public void testCreateBookingPOST() {
    Allure.step("Set base path to booking endpoint");
    requestSpecification.basePath(APIConstants.CREATE_UPDATE_BOOKING_URL);

    Allure.step("Send POST request with booking payload");
    response = RestAssured.given(requestSpecification)
        .when()
        .body(payloadManager.createPayloadBookingAsString())
        .post();

    Allure.step("Verify response status code is 200");
    validatableResponse = response.then().statusCode(200);

    Allure.step("Verify booking ID is not null");
    BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());
    assertActions.verifyStringKeyNotNull(bookingResponse.getBookingid());

    Allure.addAttachment("Response Body", "application/json", response.asString(), "json");
}
```

### 生成和打开报告

```bash
# Run tests with specific suite
mvn clean test -DsuiteXmlFile=testng_e2e.xml

# Generate Allure report
allure generate target/allure-results --clean -o allure-report
allure open allure-report
```

## 响应提取模式

```java
// Extract single value with JsonPath
String firstname = response.jsonPath().getString("booking.firstname");
Integer bookingId = response.jsonPath().getInt("bookingid");
List<Integer> allIds = response.jsonPath().getList(".", Integer.class);

// Extract as POJO with GSON
BookingResponse bookingResponse = payloadManager.bookingResponseJava(response.asString());

// Extract with ValidatableResponse
validatableResponse = response.then()
    .body("booking.firstname", equalTo("Pramod"))
    .body("booking.totalprice", greaterThan(0))
    .body("bookingid", notNullValue());

// Chain extraction
String token = response.then()
    .statusCode(200)
    .extract()
    .path("token");
```

## JSON Schema 验证

```java
import static io.restassured.module.jsv.JsonSchemaValidator.matchesJsonSchemaInClasspath;

@Test
public void testBookingResponseMatchesSchema() {
    response = RestAssured.given(requestSpecification)
        .basePath(APIConstants.CREATE_UPDATE_BOOKING_URL)
        .when()
        .body(payloadManager.createPayloadBookingAsString())
        .post();

    response.then()
        .statusCode(200)
        .body(matchesJsonSchemaInClasspath("schemas/booking-response-schema.json"));
}
```

### booking-response-schema.json

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["bookingid", "booking"],
  "properties": {
    "bookingid": { "type": "integer" },
    "booking": {
      "type": "object",
      "required": ["firstname", "lastname", "totalprice", "depositpaid", "bookingdates"],
      "properties": {
        "firstname": { "type": "string" },
        "lastname": { "type": "string" },
        "totalprice": { "type": "integer" },
        "depositpaid": { "type": "boolean" },
        "bookingdates": {
          "type": "object",
          "required": ["checkin", "checkout"],
          "properties": {
            "checkin": { "type": "string" },
            "checkout": { "type": "string" }
          }
        },
        "additionalneeds": { "type": "string" }
      }
    }
  }
}
```

## 最佳实践

1. **所有载荷使用 PayloadManager** -- 永远不要在测试中内联创建 JSON 字符串。将所有载荷逻辑集中在 PayloadManager 中以实现可维护性。
2. **分离请求和响应 POJO** -- 将请求模型与响应模型分开。响应模型可能有额外字段(ID、时间戳)。
3. **在 ITestContext 中存储状态** -- 对于 E2E 集成流,使用 `iTestContext.setAttribute()` 跨测试方法共享预订 ID 和令牌。
4. **使用测试组和优先级** -- 用组(`reg`、`qa`、`smoke`)标记测试并设置优先级以在集成流中有序执行。
5. **验证不仅仅是状态码** -- 始终断言响应体字段,而不只是 HTTP 状态。使用 AssertActions 进行一致的验证。
6. **显式测试边界情况** -- 包括空载荷、非 ASCII 字符、无效日期和边界值的测试。
7. **生成随机测试数据** -- 使用 JavaFaker 进行非确定性测试数据,以使用变化输入捕获意外失败。
8. **记录所有请求和响应** -- 在请求和响应上都使用 `.log().all()` 以便在测试失败时轻松调试。
9. **保持 API 常量集中** -- 将所有端点路径存储在 APIConstants 中。当 API 路径更改时更新一次。
10. **一致使用 Allure 注解** -- 在每个测试方法上添加 @Description、@Owner、@TmsLink 以在报告中实现完全可追溯性。

## 应避免的反模式

1. **测试中硬编码 base URL** -- 始终使用 APIConstants。硬编码 URL 在环境更改时会破坏。
2. **字符串连接用于 JSON** -- 永远不要用字符串连接手动构建 JSON。使用带 GSON 序列化的 POJO。
3. **测试之间共享可变状态** -- 使用 ITestContext 进行状态共享,而不是静态字段。静态状态会破坏并行执行。
4. **缺少断言** -- 只调用端点而没有断言的测试不是测试。始终验证响应体。
5. **每个测试中生成令牌** -- 在设置中生成一次令牌或通过 ITestContext 共享。避免冗余的 auth 调用。
6. **忽略响应时间** -- 使用 `assertActions.verifyResponseTime()` 为性能关键端点添加响应时间断言。
7. **无 schema 验证** -- 使用 JSON Schema 验证以在生产前捕获破坏性契约更改。
8. **只测试快乐路径** -- 始终包含负向测试(空体、无效 auth、错误内容类型、不存在的资源)。
9. **单体测试类** -- 按功能区域(CRUD、auth、集成)将测试分离到专用测试类中。
10. **不使用测试套件** -- 始终为不同场景(smoke、regression、e2e)配置 TestNG XML 套件,而不是每次都运行所有测试。