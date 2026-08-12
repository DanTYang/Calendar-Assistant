package com.calendarassistant.gateway.user;

import jakarta.persistence.*;
import java.time.Instant;

/**
 * One person, as this service knows them.
 *
 * <p>Identity hangs off {@code googleSubject} - the {@code sub} claim Google
 * issues - and never off the email address. Addresses change, and they get
 * reused: keying on one means a new owner of an old address inherits the
 * previous owner's calendar history. {@code sub} is opaque, stable, and
 * unique forever.
 *
 * <p>The internal {@code id} exists so nothing outside is coupled to Google.
 * Adding a second way to sign in later becomes another linked identity
 * against the same user, rather than a migration.
 */
@Entity
@Table(name = "app_user")
public class AppUser {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true, updatable = false)
    private String googleSubject;

    /** Shown in the interface. Display only - never used to find anyone. */
    private String email;

    private String displayName;

    private Instant firstSeenAt;
    private Instant lastSeenAt;

    protected AppUser() { }

    public AppUser(String googleSubject, String email, String displayName) {
        this.googleSubject = googleSubject;
        this.email = email;
        this.displayName = displayName;
        this.firstSeenAt = Instant.now();
        this.lastSeenAt = this.firstSeenAt;
    }

    /**
     * What the calendar service is told about who is asking.
     *
     * <p>The internal id rather than the Google subject: the downstream service
     * has no business knowing which provider someone signed in with, and an
     * opaque number leaks nothing if it is ever logged.
     */
    public String downstreamId() {
        return String.valueOf(id);
    }

    public void seenAgain(String email, String displayName) {
        this.email = email;
        this.displayName = displayName;
        this.lastSeenAt = Instant.now();
    }

    public Long getId() { return id; }
    public String getGoogleSubject() { return googleSubject; }
    public String getEmail() { return email; }
    public String getDisplayName() { return displayName; }
    public Instant getFirstSeenAt() { return firstSeenAt; }
    public Instant getLastSeenAt() { return lastSeenAt; }
}
