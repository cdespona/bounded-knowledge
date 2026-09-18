package com.example.mercurio.customer;

import io.confluent.kafka.schemaregistry.client.SchemaRegistryClient;
import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.kafka.core.KafkaTemplate;

public final class CustomerKafkaReferences {
    private final KafkaTemplate<String, String> kafkaTemplate;
    private final SchemaRegistryClient schemaRegistryClient;

    public CustomerKafkaReferences(
            KafkaTemplate<String, String> template,
            SchemaRegistryClient registry) {
        this.kafkaTemplate = template;
        this.schemaRegistryClient = registry;
    }

    public NewTopic customerUpdatedTopic() {
        return new NewTopic("customer.updated", 1, (short) 1);
    }

    public void publish(String payload) {
        kafkaTemplate.send("customer.updated", payload);
    }

    public void inspectSchema() {
        schemaRegistryClient.getLatestSchemaMetadata("customer-updated-value");
    }

    public String unrelatedTopicLikeString() {
        return "customer.unrelated";
    }
}
