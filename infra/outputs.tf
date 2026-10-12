output "region" {
  description = "OCI deployment region"
  value       = var.region
}

output "vcn_ocid" {
  description = "OCID of the POC VCN"
  value       = oci_core_vcn.poc.id
}

output "subnet_ocid" {
  description = "OCID of the POC subnet"
  value       = oci_core_subnet.poc.id
}

output "object_storage_namespace" {
  description = "OCI Object Storage namespace"
  value       = data.oci_objectstorage_namespace.ns.namespace
}

output "documents_bucket_name" {
  description = "Private Object Storage bucket for bot documents"
  value       = oci_objectstorage_bucket.documents.name
}