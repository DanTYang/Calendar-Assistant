package com.calendarassistant.gateway.calendar;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.io.OutputStream;
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
 * <p>{@code X-Gateway-Key} is what makes that safe. The calendar service
 * believes the user id it is sent, so without a shared secret anything able to
 * reach its port could claim to be anyone. With one, it answers only this
 * service.
 */
@Service
public class CalendarClient {

    private final RestClient http;

    public CalendarClient(@Value("${calendar.service.base-url}") String baseUrl,
                          @Value("${calendar.service.secret:}") String secret) {
        // Built directly rather than injected: there is nothing to customise
        // here, and depending on an auto-configured builder made this bean fail
        // to construct at startup.
        //
        // The secret is attached once, to every request, rather than being
        // remembered at each call site - the one that forgets is the hole.
        this.http = RestClient.builder()
                .baseUrl(baseUrl)
                .defaultHeader("X-Gateway-Key", secret)
                .build();
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

    public record Turn(String role, String text) { }

    public record History(List<Turn> turns) { }

    /**
     * What this person has said so far, and what was answered.
     *
     * <p>The conversation lives in the calendar service, so a page that has
     * just been reloaded can ask for it rather than starting blank in front of
     * an assistant that remembers the whole thing.
     */
    public History history(String userId) {
        return exchange(() -> http.get()
                .uri("/history")
                .header("X-User-Id", userId)
                .retrieve()
                .body(History.class));
    }

    /**
     * Proxies a streamed answer straight through, byte for byte.
     *
     * <p>Nothing is parsed on the way past. The events are the calendar
     * service's format and the browser's to interpret; re-encoding them here
     * would add a second thing to keep in step for no gain.
     *
     * <p>The bytes are flushed as they arrive rather than buffered, which is
     * the whole point - a proxy that waits for the last event delivers exactly
     * what the non-streaming endpoint already did.
     */
    public void streamChat(String userId, String accessToken, String message,
                           OutputStream out) {
        exchange(() -> http.post()
                .uri("/chat/stream")
                .header("X-User-Id", userId)
                .header("X-Google-Token", accessToken)
                .body(new ChatRequest(message))
                .exchange((request, response) -> {
                    if (response.getStatusCode().isError()) {
                        throw new CalendarServiceException(
                                response.getStatusCode().value(),
                                new String(response.getBody().readAllBytes()));
                    }
                    // The response is closed for us when this returns, which
                    // is correct because the body is fully drained here.
                    byte[] buffer = new byte[512];
                    int read;
                    while ((read = response.getBody().read(buffer)) != -1) {
                        out.write(buffer, 0, read);
                        out.flush();
                    }
                    return null;
                }));
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
