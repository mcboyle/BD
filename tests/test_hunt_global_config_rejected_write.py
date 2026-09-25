"""Rejected global config updates must leave live settings untouched."""

BD_GATE_SCOPE = "module"


def test_unknown_key_rejects_entire_global_config_update():
    import bulk_downloader.app as app_module
    import bulk_downloader.runner as runner

    original_config = app_module._app_cfg["global_max_concurrent"]
    original_cap = runner._global_sem_size
    proposed = 5 if original_config != 5 else 6
    try:
        response = app_module.app.test_client().post(
            "/api/global_config",
            json={"global_max_concurrent": proposed, "bogus_hunt_key": True},
        )
        assert response.status_code == 400
        assert response.get_json()["unknown_keys"] == ["bogus_hunt_key"]
        assert app_module._app_cfg["global_max_concurrent"] == original_config
        assert runner._global_sem_size == original_cap
    finally:
        app_module._app_cfg["global_max_concurrent"] = original_config
        runner.set_global_concurrent_cap(original_cap)


def test_value_rejection_rolls_back_every_live_side_effect():
    import bulk_downloader.aiassist as aiassist
    import bulk_downloader.app as app_module
    import bulk_downloader.daily_budget as daily_budget
    import bulk_downloader.log as log
    import bulk_downloader.runner as runner

    cfg_before = dict(app_module._app_cfg)
    cap, budget = runner._global_sem_size, daily_budget.get_global_budget()
    ai, level = aiassist.get_config(), log.get_level()
    try:
        response = app_module.app.test_client().post(
            "/api/global_config",
            json={
                "global_max_concurrent": 5 if cap != 5 else 6,
                "global_daily_byte_budget": budget + 1234,
                "ai_enabled": not ai["enabled"],
                "log_level": "NOT_A_LEVEL",
            },
        )
        assert response.status_code == 400
        assert "log_level" in response.get_json()["error"]
        assert dict(app_module._app_cfg) == cfg_before
        assert runner._global_sem_size == cap
        assert daily_budget.get_global_budget() == budget
        assert aiassist.get_config() == ai
        assert log.get_level() == level
    finally:
        app_module._app_cfg.clear()
        app_module._app_cfg.update(cfg_before)
        runner.set_global_concurrent_cap(cap)
        daily_budget.set_global_budget(budget)
        aiassist._config.clear()
        aiassist._config.update(ai)
