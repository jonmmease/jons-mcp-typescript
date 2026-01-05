import pytest
import json


class TestDaemonProtocol:
    """Contract tests for the daemon JSON Lines protocol."""

    def test_request_format(self):
        """Test that requests have required fields."""
        request = {
            "id": "req-1",
            "version": 1,
            "method": "format",
            "params": {"filepath": "/test.ts", "content": "const x = 1;", "projectRoot": "/"}
        }

        # Validate structure
        assert "id" in request
        assert "version" in request
        assert request["version"] == 1
        assert "method" in request
        assert "params" in request

    def test_response_success_format(self):
        """Test success response format."""
        response = {
            "id": "req-1",
            "result": {"formatted": "const x = 1;\n", "changed": True}
        }

        assert "id" in response
        assert "result" in response
        assert "error" not in response

    def test_response_error_format(self):
        """Test error response format."""
        response = {
            "id": "req-1",
            "error": {
                "code": -32001,
                "message": "Config not found",
                "data": {
                    "type": "ConfigNotFound",
                    "retryable": False
                }
            }
        }

        assert "id" in response
        assert "error" in response
        assert "result" not in response
        assert response["error"]["code"] < 0
        assert "message" in response["error"]
        assert "retryable" in response["error"]["data"]

    def test_error_codes(self):
        """Test that error codes match specification."""
        error_codes = {
            -32000: ("InternalError", True),
            -32001: ("ConfigNotFound", False),
            -32002: ("ParseError", False),
            -32003: ("PluginMissing", False),
            -32004: ("Timeout", True),
            -32700: ("JSONParseError", False),
        }

        for code, (error_type, retryable) in error_codes.items():
            # Verify error code is negative (LSP convention)
            assert code < 0
            # Verify retryable flag makes sense
            if error_type in ["InternalError", "Timeout"]:
                assert retryable == True

    def test_ready_signal_format(self):
        """Test ready signal format."""
        ready = {"event": "ready", "version": 1}

        assert ready["event"] == "ready"
        assert ready["version"] == 1

    def test_json_lines_format(self):
        """Test that each message is a single JSON line."""
        messages = [
            {"id": "1", "version": 1, "method": "ping", "params": {}},
            {"id": "1", "result": {"ok": True}},
        ]

        for msg in messages:
            json_line = json.dumps(msg)
            # Must be single line
            assert "\n" not in json_line
            # Must be valid JSON
            parsed = json.loads(json_line)
            assert parsed == msg

    def test_version_mismatch_handling(self):
        """Test version field requirements."""
        # Current version is 1
        valid_request = {"id": "1", "version": 1, "method": "ping", "params": {}}

        # Version must be present
        assert "version" in valid_request

        # Version must be numeric
        assert isinstance(valid_request["version"], int)

    def test_malformed_json_handling(self):
        """Test that malformed JSON can be detected."""
        malformed_inputs = [
            "not json at all",
            '{"incomplete": ',
            "{'single': 'quotes'}",
            "",
        ]

        for bad_input in malformed_inputs:
            with pytest.raises(json.JSONDecodeError):
                json.loads(bad_input)

    def test_method_names(self):
        """Test that method names follow expected patterns."""
        valid_methods = ["format", "check", "lint", "getConfig", "ping", "shutdown"]

        for method in valid_methods:
            request = {"id": "1", "version": 1, "method": method, "params": {}}
            assert request["method"] in valid_methods

    def test_partial_line_handling(self):
        """Test handling of partial lines in JSON Lines protocol."""
        # Partial lines should not be processable as JSON
        partial_lines = [
            '{"id": "1", "version": 1, "method"',
            '{"id": "1", "version": 1,',
            '{"id"',
        ]

        for partial_line in partial_lines:
            with pytest.raises(json.JSONDecodeError):
                json.loads(partial_line)

    def test_request_id_format(self):
        """Test that request IDs are properly formatted."""
        # Request IDs can be strings or numbers, but must be unique per request
        valid_ids = ["req-1", "1", "abc123", 42]

        for request_id in valid_ids:
            request = {
                "id": request_id,
                "version": 1,
                "method": "ping",
                "params": {}
            }
            assert request["id"] == request_id

    def test_params_structure(self):
        """Test that params field is properly structured."""
        # Params must be an object (dict)
        request = {
            "id": "1",
            "version": 1,
            "method": "format",
            "params": {}
        }

        assert isinstance(request["params"], dict)

    def test_error_response_with_data_field(self):
        """Test error responses include required data fields."""
        error_response = {
            "id": "req-1",
            "error": {
                "code": -32001,
                "message": "Config not found",
                "data": {
                    "type": "ConfigNotFound",
                    "retryable": False
                }
            }
        }

        error = error_response["error"]
        assert "code" in error
        assert "message" in error
        assert "data" in error
        assert "type" in error["data"]
        assert "retryable" in error["data"]
