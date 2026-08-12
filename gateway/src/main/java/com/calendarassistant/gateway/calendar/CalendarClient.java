package com.calendarassistant.gateway.calendar;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatusCode;
import org.springframework.stereotype.Service;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;

/**
 * Talks to the Python service that owns the calendar.
 *
 * <p>Two headers carry everything it needs to know. {@code X-User-Id} is this
 * application's own id for the person - not their Google subject, which the
 * calendar service has no business seeing. {@code X-Google-Token} is an access
 * token this service has already refreshed, so nothing downstream reads a
 * credential from disk or has to know how to renew one.
 *
 * <p>That arrangement is only safe because the calendar service is expected to
 * be unreachable except from here. It believes the user id header, so anything
 * able to call it directly can claim to be anyone.
 */
@Service
public class CalendarClient {

    private final RestClient http;

    public CalendarClient(@Value("${calendar.service.base-url}") String baseUrl) {
        // Built directly rather than injected: there is nothing to customise
        // here, and depending on an auto-configured builder made this bean fail
        // to construct at startup.
        this.http = RestClient.builder().baseUrl(baseUrl).build();
    }

    public record ChatRequest(String message) { }

    public record ChatReply(
            String answer,
            @JsonProperty("tools_used") List<Map<String, Object>> toolsUsed,
            String user) { }

    public ChatReply chat(String userId, String accessToken, String message) {
        return exchange(() -> http.post()
                .uri("/chat")
                .header("X-User-Id", userId)
                .header("X-Google-Token", accessToken)
                .body(new ChatRequest(message))
                .retrieve()
                .onStatus(HttpStatusCode::isError, (request, response) -> {
                    // The calendar service already tells a caller apart from a
                    // failure; that judgement is worth preserving rather than
                    // flattening everything into one error here.
                    throw new CalendarServiceException(
                            response.getStatusCode().value(),
                            new String(response.getBody().readAllBytes()));
                })
                .body(ChatReply.class));
    }

    public Map<String, Object> health() {
        return exchange(() -> http.get().uri("/health").retrieve().body(Map.class));
    }

    /**
     * Turns "the calendar service is not answering" into something distinct
     * from "the calendar service said no".
     *
     * <p>Both are failures and only one is worth retrying, which is the whole
     * reason for telling them apart.
     */
    private <T> T exchange(java.util.function.Supplier<T> call) {
        try {
            return call.get();
        } catch (ResourceAccessException unreachable) {
            throw new CalendarServiceException(503,
                    "the calendar service is not reachable");
        }
    }
}
