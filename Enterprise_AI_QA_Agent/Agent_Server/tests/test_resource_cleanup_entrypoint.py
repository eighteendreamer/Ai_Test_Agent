from src.cli.run_resource_cleanup_worker import main


def test_cleanup_entrypoint_is_importable():
    assert callable(main)
