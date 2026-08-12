package com.calendarassistant.gateway.web;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.client.OAuth2AuthorizedClient;
import org.springframework.security.oauth2.client.annotation.RegisteredOAuth2AuthorizedClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * What this service currently holds on someone's behalf.
 *
 * <p>Exists because the interesting failure is silent. A sign-in without a
 * refresh token looks exactly like one with a refresh token, until an hour
 * later when the access token expires and there is nothing to renew it with.
 * Being able to see which happened turns that into a check rather than a
 * surprise.
 *
 * <p>No token value is ever returned. Whether one is held, and when it
 * expires, is all a browser needs to know.
 */
@RestController
public class TokenController {

    @GetMapping("/token-status")
    public Map<String, Object> tokenStatus(
            @RegisteredOAuth2AuthorizedClient("google") OAuth2AuthorizedClient client,
            @AuthenticationPrincipal Object principal) {

        var access = client.getAccessToken();
        var refresh = client.getRefreshToken();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("hasAccessToken", access != null);
        body.put("accessTokenExpiresAt", access != null ? access.getExpiresAt() : null);
        body.put("accessTokenExpired",
                access != null && access.getExpiresAt() != null
                        && access.getExpiresAt().isBefore(Instant.now()));
        // The one that matters. False means offline access was not granted,
        // and this session cannot outlive its access token.
        body.put("hasRefreshToken", refresh != null);
        body.put("scopes", access != null ? access.getScopes() : null);
        return body;
    }
}
