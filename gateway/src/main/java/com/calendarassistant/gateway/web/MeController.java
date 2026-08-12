package com.calendarassistant.gateway.web;

import com.calendarassistant.gateway.user.AccountService;
import com.calendarassistant.gateway.user.AppUser;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class MeController {

    private final AccountService accounts;

    public MeController(AccountService accounts) {
        this.accounts = accounts;
    }

    /** Unauthenticated on purpose - it is how you check the service is up. */
    @GetMapping("/health")
    public Map<String, Object> health() {
        return Map.of("status", "ok", "service", "gateway");
    }

    /**
     * Who the current session belongs to.
     *
     * <p>Note what is not here: the Google subject. It identifies the account
     * to us and to Google, and nothing outside needs it - the browser gets the
     * internal id, which is also what the calendar service will be told.
     */
    @GetMapping("/me")
    public Map<String, Object> me(@AuthenticationPrincipal OidcUser principal) {
        AppUser user = accounts.require(principal);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", user.getId());
        body.put("email", user.getEmail());
        body.put("displayName", user.getDisplayName());
        body.put("firstSeenAt", user.getFirstSeenAt());
        body.put("lastSeenAt", user.getLastSeenAt());
        return body;
    }
}
