plugins {
    kotlin("jvm") version "2.0.21"
    alias(libs.plugins.spring.boot)
}

group = "com.example.mercurio"
version = "1.0.0-SNAPSHOT"

dependencies {
    implementation("org.springframework:spring-context:6.2.1")
    testImplementation("org.junit.jupiter:junit-jupiter:$junitVersion")
    implementation(project(":shared"))
}
