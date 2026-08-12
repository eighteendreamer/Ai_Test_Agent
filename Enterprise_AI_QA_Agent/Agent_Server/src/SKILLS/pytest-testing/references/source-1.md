---
name: Pytest Patterns
description: Python 测试,包含 pytest fixtures、parametrize、markers 和插件
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit, integration]
frameworks: [pytest]
languages: [python]
info: vip.hctestedu.com
domains: [api, web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Pytest 模式技能

你是一位专注于使用 pytest 进行测试的 Python 开发专家。当用户要求你编写、审查或调试 pytest 测试时,请遵循以下详细说明。

## 核心原则

1. **约定优于配置** -- pytest 通过命名约定自动发现测试。
2. **Fixtures 用于设置** -- 使用 fixtures 而不是 setUp/tearDown 方法。
3. **参数化覆盖** -- 使用 `@pytest.mark.parametrize` 进行数据驱动测试。
4. **描述性测试名称** -- 函数名称应描述预期行为。
5. **最小测试范围** -- 每个测试验证一个行为。

## 项目结构

```
project/
  src/
    myapp/
      __init__.py
      services/
        user_service.py
        order_service.py
      models/
        user.py
      utils/
        validators.py
  tests/
    __init__.py
    conftest.py
    unit/
      __init__.py
      test_user_service.py
      test_validators.py
    integration/
      __init__.py
      conftest.py
      test_user_api.py
    fixtures/
      user_fixtures.py
  pyproject.toml
  pytest.ini
```

## 配置

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short --strict-markers
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks integration tests
    smoke: marks smoke tests
    unit: marks unit tests
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short --strict-markers --cov=src --cov-report=term-missing"
markers = [
    "slow: marks tests as slow",
    "integration: marks integration tests",
    "smoke: marks smoke tests",
]

[tool.coverage.run]
source = ["src"]
omit = ["tests/*", "*/__init__.py"]

[tool.coverage.report]
fail_under = 80
show_missing = true
```

## Fixtures

### 基本 Fixtures

```python
# conftest.py
import pytest
from myapp.services.user_service import UserService
from myapp.models.user import User


@pytest.fixture
def sample_user():
    """Create a sample user for testing."""
    return User(
        id="user-123",
        email="test@example.com",
        name="Test User",
        role="user",
    )


@pytest.fixture
def admin_user():
    """Create an admin user for testing."""
    return User(
        id="admin-123",
        email="admin@example.com",
        name="Admin User",
        role="admin",
    )


@pytest.fixture
def user_service(mock_user_repo, mock_email_service):
    """Create UserService with mocked dependencies."""
    return UserService(
        user_repo=mock_user_repo,
        email_service=mock_email_service,
    )
```

### Fixture 作用域

```python
@pytest.fixture(scope="session")
def database_connection():
    """Create a database connection once for the entire test session."""
    conn = create_connection("test_db")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def test_data(database_connection):
    """Seed test data once per module."""
    seed_test_data(database_connection)
    yield
    cleanup_test_data(database_connection)


@pytest.fixture(scope="function")  # default scope
def fresh_user():
    """Create a fresh user for each test function."""
    return create_user(email=f"test-{uuid4()}@example.com")


@pytest.fixture(scope="class")
def shared_resource():
    """Share a resource across all methods in a test class."""
    resource = create_expensive_resource()
    yield resource
    resource.cleanup()
```

### Fixture 工厂

```python
@pytest.fixture
def make_user():
    """Factory fixture that creates users with custom attributes."""
    created_users = []

    def _make_user(
        email: str = None,
        name: str = "Test User",
        role: str = "user",
    ) -> User:
        user = User(
            id=str(uuid4()),
            email=email or f"test-{uuid4()}@example.com",
            name=name,
            role=role,
        )
        created_users.append(user)
        return user

    yield _make_user

    # Cleanup
    for user in created_users:
        try:
            delete_user(user.id)
        except Exception:
            pass


# Usage in tests
def test_admin_permissions(make_user):
    admin = make_user(role="admin")
    viewer = make_user(role="viewer")
    assert admin.can_delete_users()
    assert not viewer.can_delete_users()
```

### Yield Fixtures (Setup/Teardown)

```python
@pytest.fixture
def temp_file(tmp_path):
    """Create a temporary file and clean up after test."""
    file_path = tmp_path / "test_data.json"
    file_path.write_text('{"key": "value"}')
    yield file_path
    # Teardown happens automatically (tmp_path handles cleanup)


@pytest.fixture
def mock_server():
    """Start a mock HTTP server for testing."""
    server = MockServer(port=8089)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def db_transaction(database_connection):
    """Wrap each test in a database transaction that rolls back."""
    transaction = database_connection.begin()
    yield database_connection
    transaction.rollback()
```

## Parametrize

### 基本 Parametrize

```python
@pytest.mark.parametrize("email,expected", [
    ("user@example.com", True),
    ("first.last@domain.co.uk", True),
    ("user+tag@example.com", True),
    ("", False),
    ("not-an-email", False),
    ("@missing-local.com", False),
    ("missing-at.com", False),
])
def test_is_valid_email(email, expected):
    assert is_valid_email(email) == expected
```

### 多个参数

```python
@pytest.mark.parametrize("a,b,expected", [
    (1, 2, 3),
    (0, 0, 0),
    (-1, 1, 0),
    (100, 200, 300),
])
def test_add(a, b, expected):
    assert add(a, b) == expected
```

### 带 ID 的 Parametrize

```python
@pytest.mark.parametrize(
    "password,is_valid",
    [
        pytest.param("SecurePass1!", True, id="strong-password"),
        pytest.param("short", False, id="too-short"),
        pytest.param("nouppercase1!", False, id="no-uppercase"),
        pytest.param("NOLOWERCASE1!", False, id="no-lowercase"),
        pytest.param("NoSpecialChar1", False, id="no-special-char"),
    ],
)
def test_password_validation(password, is_valid):
    assert validate_password(password) == is_valid
```

### 组合 Parametrize 装饰器

```python
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
@pytest.mark.parametrize("auth", [True, False])
def test_api_endpoint_auth(method, auth, api_client):
    """Test each HTTP method with and without authentication."""
    response = api_client.request(method, "/protected", authenticated=auth)
    if auth:
        assert response.status_code != 401
    else:
        assert response.status_code == 401
```

## Markers

```python
# Define custom markers in conftest.py or pytest.ini

@pytest.mark.slow
def test_large_file_processing():
    """This test takes a long time to run."""
    result = process_large_file("100mb_dataset.csv")
    assert result.row_count == 1_000_000


@pytest.mark.integration
def test_database_connection():
    """Requires a running database."""
    conn = connect_to_db()
    assert conn.is_connected()


@pytest.mark.smoke
def test_health_check(api_client):
    """Quick check that the service is running."""
    response = api_client.get("/health")
    assert response.status_code == 200


@pytest.mark.skip(reason="Feature not yet implemented")
def test_future_feature():
    pass


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Not supported on Windows"
)
def test_unix_specific():
    pass


@pytest.mark.xfail(reason="Known bug #1234")
def test_known_issue():
    assert buggy_function() == expected_value
```

## 使用 pytest-mock 进行模拟

```python
# Install: pip install pytest-mock

def test_create_user_sends_email(mocker, user_service):
    """Verify that creating a user sends a welcome email."""
    mock_send = mocker.patch.object(
        user_service.email_service,
        "send_welcome_email",
    )
    mocker.patch.object(
        user_service.user_repo,
        "find_by_email",
        return_value=None,
    )
    mocker.patch.object(
        user_service.user_repo,
        "create",
        return_value=User(id="1", email="new@example.com", name="New"),
    )

    user_service.create_user("new@example.com", "New")

    mock_send.assert_called_once_with("new@example.com", "New")


def test_api_call_with_retry(mocker):
    """Test that the function retries on failure."""
    mock_get = mocker.patch("requests.get")
    mock_get.side_effect = [
        ConnectionError("Failed"),
        ConnectionError("Failed"),
        mocker.Mock(status_code=200, json=lambda: {"data": "success"}),
    ]

    result = fetch_with_retry("/api/data", max_retries=3)

    assert result == {"data": "success"}
    assert mock_get.call_count == 3


def test_datetime_mocking(mocker):
    """Mock the current time for deterministic testing."""
    fixed_now = datetime(2024, 6, 15, 12, 0, 0)
    mocker.patch("myapp.services.datetime")
    mocker.patch("myapp.services.datetime.now", return_value=fixed_now)

    result = get_greeting()
    assert result == "Good afternoon"
```

## Conftest 模式

```python
# tests/conftest.py -- shared across all tests

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_user_repo():
    """Create a mock UserRepository."""
    repo = MagicMock()
    repo.find_by_email.return_value = None
    repo.find_by_id.return_value = None
    repo.create.side_effect = lambda data: {**data, "id": "generated-id"}
    return repo


@pytest.fixture
def mock_email_service():
    """Create a mock EmailService."""
    return MagicMock()


@pytest.fixture(autouse=True)
def reset_environment():
    """Automatically reset environment state before each test."""
    import os
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)
```

```python
# tests/integration/conftest.py -- shared only within integration tests

import pytest
import httpx


@pytest.fixture(scope="session")
def api_base_url():
    return os.getenv("API_BASE_URL", "http://localhost:3000")


@pytest.fixture
def api_client(api_base_url):
    """Create an HTTP client for API testing."""
    with httpx.Client(base_url=api_base_url) as client:
        yield client


@pytest.fixture
def auth_client(api_client, api_base_url):
    """Create an authenticated HTTP client."""
    response = api_client.post("/api/auth/login", json={
        "email": "admin@example.com",
        "password": "AdminPass123!",
    })
    token = response.json()["token"]

    with httpx.Client(
        base_url=api_base_url,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client
```

## 异常测试

```python
def test_raises_value_error():
    with pytest.raises(ValueError, match="Invalid email"):
        validate_email("")


def test_raises_not_found():
    with pytest.raises(NotFoundError) as exc_info:
        get_user("nonexistent")

    assert exc_info.value.resource == "User"
    assert exc_info.value.id == "nonexistent"


def test_does_not_raise():
    # This should not raise any exception
    result = safe_divide(10, 2)
    assert result == 5.0
```

## 临时文件和目录

```python
def test_file_processing(tmp_path):
    """Use tmp_path for temporary file operations."""
    input_file = tmp_path / "input.csv"
    input_file.write_text("name,email\nJohn,john@example.com\n")

    output_file = tmp_path / "output.json"
    convert_csv_to_json(input_file, output_file)

    result = json.loads(output_file.read_text())
    assert len(result) == 1
    assert result[0]["name"] == "John"


def test_config_loading(tmp_path):
    """Test configuration file loading."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("database:\n  host: localhost\n  port: 5432\n")

    config = load_config(str(config_file))
    assert config["database"]["host"] == "localhost"
    assert config["database"]["port"] == 5432
```

## 最佳实践

1. **使用 fixtures 进行共享设置** -- 避免在测试间重复设置代码。
2. **在正确级别使用 `conftest.py`** -- 将 fixtures 放置在需要的地方。
3. **描述性地命名测试** -- `test_create_user_with_duplicate_email_raises_conflict`。
4. **使用 `parametrize` 进行数据驱动测试** -- 显著减少代码重复。
5. **使用 markers 进行测试分类** -- 使用 `-m "smoke"` 或 `-m "not slow"` 运行子集。
6. **使用 `tmp_path` 进行文件操作** -- 内置 fixture 自动处理清理。
7. **使用 `mocker` 从 pytest-mock** -- 比直接使用 `unittest.mock` 更清晰。
8. **设置 `--strict-markers`** -- 捕获 marker 名称中的拼写错误。
9. **谨慎使用 `autouse` fixtures** -- 仅用于真正的通用设置,如环境重置。
10. **保持 conftest 文件小** -- 将大型 conftest 文件分割成单独的 fixture 模块。

## 应避免的反模式

1. **将 `unittest.TestCase` 与 pytest 一起使用** -- 你会失去 pytest fixtures 和 parametrize。
2. **测试模块中的全局状态** -- 使用 fixtures,而不是模块级变量。
3. **过于广泛的 fixtures** -- `setup_everything` fixture 使测试耦合和缓慢。
4. **测试内部细节** -- 测试公共接口,而不是私有方法。
5. **做太多的 fixtures** -- 每个 fixture 应该做一件事。
6. **不使用 `yield` 进行 teardown** -- 确保即使测试失败也运行清理。
7. **忽略 fixture 作用域** -- 当需要 `function` 作用域时使用 `session` 作用域会导致耦合。
8. **硬编码文件路径** -- 使用 `tmp_path` 或 `importlib.resources` 代替。
9. **模拟一切** -- 如果你模拟所有依赖,你什么都没测试真实。
10. **不随机顺序运行测试** -- 安装 `pytest-randomly` 以捕获隐藏的依赖。

## 运行测试

```bash
# Run all tests
pytest

# Run specific file
pytest tests/unit/test_user_service.py

# Run specific test
pytest tests/unit/test_user_service.py::test_create_user

# Run by marker
pytest -m smoke
pytest -m "not slow"
pytest -m "unit and not integration"

# Run with coverage
pytest --cov=src --cov-report=html

# Run in parallel (requires pytest-xdist)
pytest -n auto

# Run with verbose output
pytest -v

# Run and stop on first failure
pytest -x

# Run last failed tests
pytest --lf

# Run with specific log level
pytest --log-cli-level=DEBUG
```