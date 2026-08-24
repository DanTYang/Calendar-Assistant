# The gateway.
#
# Two stages: the first has Maven and the whole dependency tree, the second has
# a JRE and one jar. The build stage is thrown away, so none of it ships.
FROM eclipse-temurin:21-jdk AS build
WORKDIR /build

# The wrapper and the POM first, then a resolve. Dependencies change rarely and
# the source changes constantly, so this layer survives almost every rebuild.
COPY mvnw .
COPY .mvn/ ./.mvn/
COPY pom.xml .
RUN chmod +x mvnw && ./mvnw -B -q dependency:go-offline

COPY src/ ./src/
RUN ./mvnw -B -q clean package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /build/target/*.jar app.jar

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8080

# Container-aware by default on this JRE: the heap is sized from the memory
# limit rather than from the host's total, which is what stops a 2GB task from
# assuming it has the whole machine.
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
