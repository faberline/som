ROUTES: tuple[tuple[str, str], ...] = {{routes}}


class ComposedServiceTest(unittest.TestCase):
    """Check identity on every selected resource and flow route."""

    def test_selected_routes_require_identity(self) -> None:
        """An anonymous request must not reach any operation."""
        application: object = importlib.import_module(APPLICATION + ".app").app
        if not isinstance(application, FastAPI):
            raise TypeError("service app must be a FastAPI application")
        routes = (*ROUTES, ("post", "/flows/" + {{flow_name}} + "/runs"),
                  ("get", "/flow-runs/missing"),
                  ("post", "/flow-runs/missing/recover"))
        with TestClient(application) as client:
            for method, route in routes:
                path = route.replace("{parent_id}", "1").replace("{child_id}", "1")
                with self.subTest(method=method, path=path):
                    response = client.request(method, path, json={})
                    self.assertEqual(response.status_code, 401, response.text)


if __name__ == "__main__":
    unittest.main()
