package com.calendarassistant.gateway.calendar;

/** A failure that came from, or on the way to, the calendar service. */
public class CalendarServiceException extends RuntimeException {

    private final int status;

    public CalendarServiceException(int status, String message) {
        super(message);
        this.status = status;
    }

    public int getStatus() {
        return status;
    }
}
