package com.calendarassistant.gateway.user;

import org.springframework.security.oauth2.core.oidc.user.OidcUser;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Turns a Google sign-in into a user of this application.
 *
 * <p>Signing in is not registration. The first time somebody arrives a row is
 * written; every time after that the same row is found again by {@code sub} and
 * its display fields are refreshed. There is no separate sign-up step, and
 * changing a Google address does not create a second account.
 */
@Service
public class AccountService {

    private final AppUserRepository users;

    public AccountService(AppUserRepository users) {
        this.users = users;
    }

    @Transactional
    public AppUser recordSignIn(OidcUser principal) {
        String subject = principal.getSubject();
        String email = principal.getEmail();
        String name = principal.getFullName() != null ? principal.getFullName() : email;

        return users.findByGoogleSubject(subject)
                .map(existing -> {
                    existing.seenAgain(email, name);
                    return existing;
                })
                .orElseGet(() -> users.save(new AppUser(subject, email, name)));
    }

    @Transactional(readOnly = true)
    public AppUser require(OidcUser principal) {
        return users.findByGoogleSubject(principal.getSubject())
                .orElseThrow(() -> new IllegalStateException(
                        "signed in but no account row - sign in again"));
    }
}
