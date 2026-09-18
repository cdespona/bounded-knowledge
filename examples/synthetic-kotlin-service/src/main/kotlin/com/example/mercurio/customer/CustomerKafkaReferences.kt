package com.example.mercurio.customer

import io.confluent.kafka.schemaregistry.client.SchemaRegistryClient
import org.apache.kafka.clients.admin.NewTopic
import org.springframework.kafka.annotation.KafkaListener
import org.springframework.kafka.core.KafkaTemplate

class CustomerKafkaReferences(
    private val kafkaTemplate: KafkaTemplate<String, String>,
    private val schemaRegistryClient: SchemaRegistryClient,
) {
    fun customerUpdatedTopic(): NewTopic = NewTopic("customer.updated", 1, 1.toShort())

    fun publish(payload: String) {
        kafkaTemplate.send("customer.updated", payload)
    }

    @KafkaListener(topics = ["customer.updated"])
    fun receive(payload: String) = payload

    fun inspectSchema() {
        schemaRegistryClient.getLatestSchemaMetadata("customer-updated-value")
    }

    fun unrelatedTopicLikeString(): String = "customer.unrelated"
}
