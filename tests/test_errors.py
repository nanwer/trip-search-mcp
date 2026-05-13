from flights_mcp.errors import ErrorCode, ToolError, error_response


def test_error_code_values_match_spec():
    assert ErrorCode.NO_RESULTS.value == "no_results"
    assert ErrorCode.INVALID_INPUT.value == "invalid_input"
    assert ErrorCode.RATE_LIMITED.value == "rate_limited"
    assert ErrorCode.UPSTREAM_ERROR.value == "upstream_error"
    # auth_failed was dropped during the fli migration (fli needs no auth)
    # and re-introduced with hotels (SerpAPI key required for search_hotels).
    # quota_exceeded stays retired — SerpAPI quota issues map to upstream_error.
    assert ErrorCode.AUTH_FAILED.value == "auth_failed"
    assert len(ErrorCode) == 5


def test_error_response_shape():
    out = error_response(ErrorCode.NO_RESULTS, "No flights found.", retryable=False)
    assert out == {
        "error": {
            "code": "no_results",
            "message": "No flights found.",
            "retryable": False,
        }
    }


def test_error_response_retryable_true():
    out = error_response(ErrorCode.RATE_LIMITED, "Slow down.", retryable=True)
    assert out["error"]["retryable"] is True


def test_tool_error_carries_code_and_message():
    err = ToolError(ErrorCode.UPSTREAM_ERROR, "transient blip")
    assert err.code == ErrorCode.UPSTREAM_ERROR
    assert err.message == "transient blip"
    assert err.retryable is False
    assert str(err) == "transient blip"
    assert isinstance(err, Exception)
