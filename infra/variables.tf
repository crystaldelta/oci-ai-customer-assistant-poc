variable "region" {
  description = "OCI region for the POC"
  type        = string
  default     = "ap-sydney-1"
}

variable "tenancy_ocid" {
  description = "OCID of the OCI tenancy"
  type        = string
}

variable "compartment_ocid" {
  description = "Approved compartment OCID for the POC"
  type        = string
}

variable "allowed_client_cidr" {
  description = "Public IP CIDR allowed to access the future bot endpoint"
  type        = string
  default     = "163.61.253.24/32"

  validation {
    condition     = can(cidrnetmask(var.allowed_client_cidr))
    error_message = "Provide a valid IPv4 CIDR, such as your-public-IP/32."
  }
}

variable "bucket_name" {
  description = "Name of the private bot documents bucket"
  type        = string
  default     = "cd-ai-bot-poc-documents"
}