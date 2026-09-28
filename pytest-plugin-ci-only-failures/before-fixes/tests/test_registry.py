from scenariokit import scenario


def test_scenario_records_its_name():
    @scenario("checkout")
    async def checkout(ctx):
        pass

    assert checkout._scenario_name == "checkout"
