package com.calendarassistant.gateway.user;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AppUserRepository extends JpaRepository<AppUser, Long> {

    /** The only supported way to find someone. See {@link AppUser}. */
    Optional<AppUser> findByGoogleSubject(String googleSubject);
}
