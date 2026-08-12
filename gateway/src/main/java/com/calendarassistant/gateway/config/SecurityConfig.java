package com.calendarassistant.gateway.config;

import com.calendarassistant.gateway.user.AccountService;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.oauth2.client.oidc.userinfo.OidcUserRequest;
import org.springframework.security.oauth2.client.registration.ClientRegistrationRepository;
import org.springframework.security.oauth2.client.web.DefaultOAuth2AuthorizationRequestResolver;
import org.springframework.security.oauth2.client.web.OAuth2AuthorizationRequestResolver;
import org.springframework.security.oauth2.client.oidc.userinfo.OidcUserService;
import org.springframework.security.oauth2.client.userinfo.OAuth2UserService;
import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;

/**
 * Who may reach what, and what happens the moment somebody signs in.
 *
 * <p>Only two things are public: the health check, and the sign-in flow itself.
 * Everything else requires a session, so an unauthenticated request to any real
 * endpoint is redirected to Google rather than answered.
 */
@Configuration
public class SecurityConfig {

    @Bean
    SecurityFilterChain securityFilterChain(
            HttpSecurity http,
            OAuth2UserService<OidcUserRequest, OidcUser> oidcUserService,
            OAuth2AuthorizationRequestResolver authorizationRequestResolver) throws Exception {

        http
            // Kept on, not switched off. Sessions here are carried by a cookie,
            // and a cookie is precisely what lets another site post to this one
            // on a signed-in user's behalf. The token goes in a readable cookie
            // so a browser front end can echo it back in a header; a JSON API
            // with no cookie would be the case for turning this off, and that
            // is not this.
            .csrf(csrf -> csrf
                .csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse()))
            .authorizeHttpRequests(requests -> requests
                .requestMatchers("/health", "/calendar-health", "/error",
                                 "/signed-out.html").permitAll()
                .anyRequest().authenticated())
            .oauth2Login(login -> login
                .authorizationEndpoint(endpoint -> endpoint
                    .authorizationRequestResolver(authorizationRequestResolver))
                .userInfoEndpoint(info -> info.oidcUserService(oidcUserService)))
            .logout(logout -> logout
                // Somewhere that says what happened, rather than the health
                // check. Landing back on "/" would bounce straight to Google
                // and sign the user back in, which does not look like logging
                // out at all.
                .logoutSuccessUrl("/signed-out.html")
                .invalidateHttpSession(true)
                .deleteCookies("JSESSIONID"));

        return http.build();
    }

    /**
     * Asks Google for a refresh token, which it does not give away by default.
     *
     * <p>Two parameters are needed, and both matter. {@code access_type=offline}
     * is what makes Google issue a refresh token at all - without it the access
     * token expires in an hour and there is no way to get another, which is
     * fine for a browser session and useless for a server meant to act on
     * someone's behalf afterwards.
     *
     * <p>{@code prompt=consent} is the awkward one. Google only issues a
     * refresh token on the consent that first grants access, so a user who has
     * approved this application before comes back with an access token and
     * nothing to renew it with - and the failure appears an hour later, not at
     * sign-in. Forcing the prompt trades a consent screen on every sign-in for
     * a token that is actually usable. It can come out again once tokens are
     * stored and reused across sessions.
     */
    @Bean
    OAuth2AuthorizationRequestResolver authorizationRequestResolver(
            ClientRegistrationRepository registrations) {
        DefaultOAuth2AuthorizationRequestResolver resolver =
                new DefaultOAuth2AuthorizationRequestResolver(
                        registrations, "/oauth2/authorization");
        // Written onto the URI rather than into the request's additional
        // parameters: the customizer runs either way, but only this form ends
        // up in the redirect Google actually receives.
        resolver.setAuthorizationRequestCustomizer(request -> request
                .authorizationRequestUri(uri -> uri
                        .queryParam("access_type", "offline")
                        .queryParam("prompt", "consent")
                        .build()));
        return resolver;
    }

    /**
     * Wraps the default so a user row exists before the session does.
     *
     * <p>Delegation rather than subclassing: the standard service already knows
     * how to fetch and verify the claims, and the only thing worth adding is
     * what we do with the result. Doing it here rather than in a controller
     * means every route can assume the account exists.
     */
    @Bean
    OAuth2UserService<OidcUserRequest, OidcUser> oidcUserService(AccountService accounts) {
        OidcUserService delegate = new OidcUserService();
        return request -> {
            OidcUser principal = delegate.loadUser(request);
            accounts.recordSignIn(principal);
            return principal;
        };
    }
}
