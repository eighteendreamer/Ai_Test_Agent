---
name: Docker Testcontainers
description: 使用 Testcontainers 进行集成测试，支持 PostgreSQL、MySQL、Redis、MongoDB 等
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [integration]
frameworks: [testcontainers]
info: vip.hctestedu.com
languages: [typescript, javascript, java]
domains: [api, database]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Docker Testcontainers 测试

您是一位专注于使用 Testcontainers 进行集成测试的 QA 工程师。当用户要求您编写、审查或调试使用 Testcontainers 的测试时，请遵循这些详细说明。

## 核心原则

1. **真实数据库** -- 使用真实的数据库容器，而非内存模拟。
2. **隔离测试** -- 每个测试或测试套件使用独立的容器。
3. **快速启动** -- 容器启动应该在几秒内完成。
4. **自动清理** -- 测试结束后自动清理容器。
5. **跨平台** -- 支持 Linux、macOS 和 Windows。

## 什么是 Testcontainers

Testcontainers 是一个 Java 库，支持在 Docker 容器中启动一次性数据库、消息队列、Selenium 浏览器等。它提供：
- 真实的基础设施用于集成测试
- 隔离的测试环境
- 自动清理
- CI/CD 友好

## 项目结构

```
integration-tests/
├── src/
│   ├── test/
│   │   ├── java/
│   │   │   └── com/example/
│   │   │       ├── BaseIntegrationTest.java
│   │   │       ├── UserRepositoryTest.java
│   │   │       └── OrderRepositoryTest.java
│   │   └── resources/
│   │       └── application.yml
│   └── main/
│       └── java/
│           └── com/example/
├── pom.xml
└── docker-compose.yml
```

## Java 配置

### Maven 依赖

```xml
<dependencies>
    <!-- Testcontainers Core -->
    <dependency>
        <groupId>org.testcontainers</groupId>
        <artifactId>testcontainers</artifactId>
        <version>1.19.3</version>
        <scope>test</scope>
    </dependency>

    <!-- PostgreSQL -->
    <dependency>
        <groupId>org.testcontainers</groupId>
        <artifactId>postgresql</artifactId>
        <version>1.19.3</version>
        <scope>test</scope>
    </dependency>

    <!-- JUnit 5 -->
    <dependency>
        <groupId>org.testcontainers</groupId>
        <artifactId>junit-jupiter</artifactId>
        <version>1.19.3</version>
        <scope>test</scope>
    </dependency>

    <!-- JDBC -->
    <dependency>
        <groupId>org.postgresql</groupId>
        <artifactId>postgresql</artifactId>
        <version>42.7.1</version>
        <scope>test</scope>
    </dependency>

    <!-- Hibernate ORM -->
    <dependency>
        <groupId>org.hibernate.orm</groupId>
        <artifactId>hibernate-core</artifactId>
        <version>6.3.1.Final</version>
    </dependency>

    <!-- Test dependencies -->
    <dependency>
        <groupId>org.junit.jupiter</groupId>
        <artifactId>junit-jupiter</artifactId>
        <version>5.10.1</version>
        <scope>test</scope>
    </dependency>

    <dependency>
        <groupId>org.assertj</groupId>
        <artifactId>assertj-core</artifactId>
        <version>3.24.2</version>
        <scope>test</scope>
    </dependency>
</dependencies>
```

## 基础测试类

### PostgreSQL 测试

```java
package com.example;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;

public abstract class BaseIntegrationTest {

    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>(
        DockerImageName.parse("postgres:15-alpine")
    )
    .withDatabaseName("testdb")
    .withUsername("test")
    .withPassword("test")
    .withReuse(true);  // 启用容器重用加快测试

    protected static String jdbcUrl;
    protected static String username;
    protected static String password;
    protected Connection connection;

    @BeforeAll
    static void startContainer() {
        postgres.start();
        jdbcUrl = postgres.getJdbcUrl();
        username = postgres.getUsername();
        password = postgres.getPassword();
    }

    @AfterAll
    static void stopContainer() {
        postgres.stop();
    }

    @BeforeEach
    void setUp() throws SQLException {
        connection = DriverManager.getConnection(jdbcUrl, username, password);
    }

    @AfterEach
    void tearDown() throws SQLException {
        if (connection != null && !connection.isClosed()) {
            connection.close();
        }
    }
}
```

### MySQL 测试

```java
@Testcontainers
class MySQLIntegrationTest {

    @Container
    static MySQLContainer<?> mysql = new MySQLContainer<>(
        DockerImageName.parse("mysql:8")
    )
    .withDatabaseName("testdb")
    .withUsername("test")
    .withPassword("test")
    .withInitScript("db/init.sql");  // 初始化脚本

    @Test
    void testDatabaseConnection() {
        assertThat(mysql.isRunning()).isTrue();
        assertThat(mysql.getMappedPort(3306)).isGreaterThan(0);
    }

    @Test
    void testQueryExecution() throws SQLException {
        try (Connection conn = DriverManager.getConnection(
                mysql.getJdbcUrl(),
                mysql.getUsername(),
                mysql.getPassword())) {

            ResultSet rs = conn.createStatement()
                .executeQuery("SELECT 1 as value");
            rs.next();
            assertThat(rs.getInt("value")).isEqualTo(1);
        }
    }
}
```

### Redis 测试

```java
@Testcontainers
class RedisIntegrationTest {

    @Container
    static GenericContainer<?> redis = new GenericContainer<>(
        DockerImageName.parse("redis:7-alpine")
    )
    .withExposedPorts(6379);

    @Test
    void testRedisConnection() {
        assertThat(redis.isRunning()).isTrue();

        // 使用 Jedis 连接
        Jedis jedis = new Jedis(
            redis.getHost(),
            redis.getMappedPort(6379)
        );

        jedis.set("test-key", "test-value");
        assertThat(jedis.get("test-key")).isEqualTo("test-value");

        jedis.close();
    }

    @Test
    void testRedisCacheOperations() {
        Jedis jedis = new Jedis(
            redis.getHost(),
            redis.getMappedPort(6379)
        );

        // 模拟缓存
        String userId = "user-123";
        String userData = "{\"name\": \"John\", \"email\": \"john@example.com\"}";

        jedis.setex(userId, 3600, userData);  // 1小时过期

        String cached = jedis.get(userId);
        assertThat(cached).isEqualTo(userData);

        jedis.del(userId);
        assertThat(jedis.get(userId)).isNull();

        jedis.close();
    }
}
```

## Repository 测试

### 用户 Repository 测试

```java
package com.example;

import org.junit.jupiter.api.Test;
import java.sql.ResultSet;
import java.sql.Statement;
import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.*;

class UserRepositoryTest extends BaseIntegrationTest {

    @Test
    void shouldSaveAndRetrieveUser() throws SQLException {
        // Given
        String email = "test-" + System.currentTimeMillis() + "@example.com";
        String name = "Test User";

        // When
        long userId;
        try (Statement stmt = connection.createStatement()) {
            stmt.executeUpdate(String.format(
                "INSERT INTO users (email, name, created_at) VALUES ('%s', '%s', '%s')",
                email, name, Timestamp.valueOf(LocalDateTime.now())
            ));

            ResultSet rs = stmt.executeQuery(
                "SELECT id, email, name FROM users WHERE email = '" + email + "'"
            );
            rs.next();
            userId = rs.getLong("id");
        }

        // Then
        try (Statement stmt = connection.createStatement()) {
            ResultSet rs = stmt.executeQuery(
                "SELECT id, email, name FROM users WHERE id = " + userId
            );
            rs.next();

            assertThat(rs.getLong("id")).isEqualTo(userId);
            assertThat(rs.getString("email")).isEqualTo(email);
            assertThat(rs.getString("name")).isEqualTo(name);
        }
    }

    @Test
    void shouldFindUserByEmail() throws SQLException {
        // Given
        String email = "findme-" + System.currentTimeMillis() + "@example.com";

        try (Statement stmt = connection.createStatement()) {
            stmt.executeUpdate(String.format(
                "INSERT INTO users (email, name, created_at) VALUES ('%s', 'Find Me', '%s')",
                email, Timestamp.valueOf(LocalDateTime.now())
            ));
        }

        // When
        User found = null;
        try (Statement stmt = connection.createStatement()) {
            ResultSet rs = stmt.executeQuery(
                "SELECT id, email, name FROM users WHERE email = '" + email + "'"
            );
            if (rs.next()) {
                found = new User(rs.getLong("id"), rs.getString("email"), rs.getString("name"));
            }
        }

        // Then
        assertThat(found).isNotNull();
        assertThat(found.email()).isEqualTo(email);
        assertThat(found.name()).isEqualTo("Find Me");
    }

    @Test
    void shouldDeleteUser() throws SQLException {
        // Given
        String email = "delete-" + System.currentTimeMillis() + "@example.com";
        long userId;

        try (Statement stmt = connection.createStatement()) {
            stmt.executeUpdate(String.format(
                "INSERT INTO users (email, name, created_at) VALUES ('%s', 'Delete Me', '%s')",
                email, Timestamp.valueOf(LocalDateTime.now())
            ));

            ResultSet rs = stmt.executeQuery(
                "SELECT id FROM users WHERE email = '" + email + "'"
            );
            rs.next();
            userId = rs.getLong("id");
        }

        // When
        try (Statement stmt = connection.createStatement()) {
            stmt.executeUpdate("DELETE FROM users WHERE id = " + userId);
        }

        // Then
        try (Statement stmt = connection.createStatement()) {
            ResultSet rs = stmt.executeQuery(
                "SELECT COUNT(*) as count FROM users WHERE id = " + userId
            );
            rs.next();
            assertThat(rs.getInt("count")).isZero();
        }
    }
}

record User(Long id, String email, String name) {}
```

## 多容器测试

### 完整微服务测试

```java
@Testcontainers
class OrderServiceIntegrationTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>(
        DockerImageName.parse("postgres:15-alpine")
    )
    .withDatabaseName("orders")
    .withUsername("test")
    .withPassword("test");

    @Container
    static GenericContainer<?> redis = new GenericContainer<>(
        DockerImageName.parse("redis:7-alpine")
    )
    .withExposedPorts(6379);

    @Container
    static GenericContainer<?> kafka = new GenericContainer<>(
        DockerImageName.parse("confluentinc/cp-kafka:7.5.0")
    )
    .withEnv("KAFKA_BROKER_ID", "1")
    .withEnv("KAFKA_ZOOKEEPER_CONNECT", "zookeeper:2181")
    .withEnv("KAFKA_ADVERTISED_LISTENERS", "PLAINTEXT://localhost:9092")
    .withEnv("KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR", "1")
    .withExposedPorts(9092);

    private static String jdbcUrl;
    private static int redisPort;

    @BeforeAll
    static void setUp() {
        jdbcUrl = postgres.getJdbcUrl();
        redisPort = redis.getMappedPort(6379);
    }

    @Test
    void shouldProcessOrderWithAllDependencies() {
        // 测试订单处理需要数据库、缓存和消息队列
        assertThat(postgres.isRunning()).isTrue();
        assertThat(redis.isRunning()).isTrue();
        assertThat(kafka.isRunning()).isTrue();

        // 验证连接
        assertThatCode(() -> {
            Connection conn = DriverManager.getConnection(
                jdbcUrl, "test", "test");
            conn.close();
        }).doesNotThrowAnyException();
    }
}
```

## 生命周期回调

```java
@Testcontainers
class LifecycleTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>(
        DockerImageName.parse("postgres:15-alpine")
    );

    // 容器启动后执行
    @BeforeAll
    static void initializeDatabase() throws SQLException {
        try (Connection conn = DriverManager.getConnection(
                postgres.getJdbcUrl(),
                postgres.getUsername(),
                postgres.getPassword())) {

            // 创建表
            conn.createStatement().execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """);
        }
    }

    @Test
    void testWithInitializedDatabase() throws SQLException {
        // 使用已初始化的数据库
        try (Connection conn = DriverManager.getConnection(
                postgres.getJdbcUrl(),
                postgres.getUsername(),
                postgres.getPassword())) {

            conn.createStatement().executeUpdate(
                "INSERT INTO users (email, name) VALUES ('test@example.com', 'Test')"
            );

            ResultSet rs = conn.createStatement()
                .executeQuery("SELECT COUNT(*) FROM users");
            rs.next();
            assertThat(rs.getInt(1)).isGreaterThan(0);
        }
    }
}
```

## 模块化测试容器

### 自定义容器模块

```java
public class MySQLTestContainer implements TestContainerResource<MySQLContainer<?>> {

    private final MySQLContainer<?> container;

    public MySQLTestContainer() {
        this.container = new MySQLContainer<>(
            DockerImageName.parse("mysql:8")
        )
        .withDatabaseName("testdb")
        .withUsername("test")
        .withPassword("test")
        .withReuse(true);
    }

    @Override
    public MySQLContainer<?> getTestContainer() {
        return container;
    }

    public String getJdbcUrl() {
        return container.getJdbcUrl();
    }

    public String getUsername() {
        return container.getUsername();
    }

    public String getPassword() {
        return container.getPassword();
    }
}
```

## CI/CD 集成

```yaml
# GitHub Actions
name: Integration Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      docker:
        image: docker:24-dind
        options: --privileged

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'

      - name: Start Docker daemon
        run: |
          docker info
          sleep 5

      - name: Run integration tests
        run: mvn verify -DskipUnitTests=false

      - name: Upload test results
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: integration-test-results
          path: target/surefire-reports
```

## 最佳实践

1. **启用容器重用** -- 使用 `.withReuse(true)` 加快本地开发测试。
2. **使用轻量级镜像** -- 使用 alpine 变体减少镜像大小。
3. **清理测试数据** -- 在 `@AfterEach` 或 `@AfterAll` 中清理数据。
4. **使用连接池** -- 测试中使用连接池提高性能。
5. **模块化容器配置** -- 创建可重用的容器配置类。
6. **合理的超时设置** -- 设置容器启动超时。
7. **健康检查** -- 等待容器健康后再运行测试。
8. **并行测试** -- 使用 JUnit 的并行执行提高速度。

## 应避免的反模式

1. **每个测试启动新容器** -- 使用类级别的容器共享。
2. **不等待容器就绪** -- 必须等待数据库完全启动。
3. **不清理测试数据** -- 测试之间的数据污染导致 flaky 测试。
4. **使用生产数据** -- 测试应该使用独立的测试数据。
5. **忽略容器日志** -- 查看日志诊断问题。
6. **过多容器** -- 减少同时运行的容器数量。
7. **不关闭容器** -- 确保资源正确释放。
8. **硬编码端口** -- 使用动态映射的端口。