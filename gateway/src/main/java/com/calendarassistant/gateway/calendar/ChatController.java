package com.calendarassistant.gateway.calendar;

import com.calendarassistant.gateway.user.AccountService;
import com.calendarassistant.gateway.user.AppUser;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.web.servlet.mvc.method.annotation.StreamingResponseBody;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.client.OAuth2AuthorizedClient;
import org.springframework.security.oauth2.client.annotation.RegisteredOAuth2AuthorizedClient;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.*;

/**
 * Where the two halves meet.
 *
 * <p>A signed-in browser session arrives here; a user id and an access token
 * leave for the calendar service. Nothing about calendars is decided in this
 * class, and nothing about identity is decided in the calendar service.
 */
@RestController
public class ChatController {

    private final AccountService accounts;
    private final CalendarClient calendar;

    public ChatController(AccountService accounts, CalendarClient calendar) {
        this.accounts = accounts;
        this.calendar = calendar;
    }

    public record Message(String message) { }

    @PostMapping("/chat")
    public CalendarClient.ChatReply chat(
            @RequestBody Message body,
            @AuthenticationPrincipal OidcUser principal,
            @RegisteredOAuth2AuthorizedClient("google") OAuth2AuthorizedClient authorized) {

        AppUser user = accounts.require(principal);

        // Spring hands over a token it has already refreshed if it needed to,
        // which is why the calendar service never has to.
        String accessToken = authorized.getAccessToken().getTokenValue();

        return calendar.chat(user.downstreamId(), accessToken, body.message());
    }

    /**
     * The same answer as {@code /chat}, sent as it is written.
     *
     * <p>Returning a {@link StreamingResponseBody} hands the response back to
     * the container immediately and writes into it from another thread, so the
     * request thread is not held for the length of an answer.
     */
    @PostMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public StreamingResponseBody chatStream(
            @RequestBody Message body,
            @AuthenticationPrincipal OidcUser principal,
            @RegisteredOAuth2AuthorizedClient("google") OAuth2AuthorizedClient authorized) {

        AppUser user = accounts.require(principal);
        String accessToken = authorized.getAccessToken().getTokenValue();

        // Resolved before the lambda: the security context belongs to the
        // request thread, and this body runs on another one.
        String userId = user.downstreamId();
        return out -> calendar.streamChat(userId, accessToken, body.message(), out);
    }

    /**
     * The conversation this session already had.
     *
     * <p>No token is sent: reading back what was said needs no calendar access,
     * and the fewer places a credential travels the better.
     */
    @GetMapping("/history")
    public CalendarClient.History history(@AuthenticationPrincipal OidcUser principal) {
        return calendar.history(accounts.require(principal).downstreamId());
    }

    /**
     * The CSRF token this session must echo back on any POST.
     *
     * <p>The token is created lazily, so something has to ask for it before the
     * cookie exists. A page that fetches this on load gets both effects at
     * once: the cookie is written, and it is handed the value directly rather
     * than having to read it back out.
     */
    @GetMapping("/csrf")
    public CsrfToken csrf(CsrfToken token) {
        return token;
    }

    /** Whether the service behind this one is answering. */
    @GetMapping("/calendar-health")
    public Map<String, Object> calendarHealth() {
        return calendar.health();
    }

    @ExceptionHandler(CalendarServiceException.class)
    ResponseEntity<Map<String, Object>> handle(CalendarServiceException error) {
        return ResponseEntity.status(error.getStatus())
                .body(Map.of("error", error.getMessage()));
    }
}
