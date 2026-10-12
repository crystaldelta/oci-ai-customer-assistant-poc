data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.tenancy_ocid
}

resource "oci_core_vcn" "poc" {
  compartment_id = var.compartment_ocid
  display_name   = "cd-ai-bot-poc-vcn"
  cidr_blocks    = ["10.20.0.0/16"]
  dns_label      = "cdaibotpoc"

  freeform_tags = {
    Project     = "CrystalDelta-AI-Bot-POC"
    Environment = "POC"
  }
}

resource "oci_core_internet_gateway" "poc" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.poc.id
  display_name   = "cd-ai-bot-poc-igw"
  enabled        = true
}

resource "oci_core_route_table" "poc" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.poc.id
  display_name   = "cd-ai-bot-poc-route-table"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.poc.id
  }
}

resource "oci_core_security_list" "poc" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.poc.id
  display_name   = "cd-ai-bot-poc-security-list"

  # Allow application access only from the configured client IP.
  # The rule is for the future application endpoint on port 8080.
  ingress_security_rules {
    protocol    = "6"
    source      = var.allowed_client_cidr
    source_type = "CIDR_BLOCK"
    stateless   = false

    tcp_options {
      min = 8080
      max = 8080
    }

    description = "Restricted access to bot application"
  }

  # Allow outbound traffic for image pulls and API calls.
  egress_security_rules {
    protocol    = "all"
    destination = "0.0.0.0/0"
    stateless   = false
  }

  # SSH access from your approved public IP only
  ingress_security_rules {
    protocol    = "6"
    source      = var.allowed_client_cidr
    source_type = "CIDR_BLOCK"
    stateless   = false

    tcp_options {
      min = 22
      max = 22
    }

    description = "Restricted SSH access to model VM"
  }

  # Ollama API access from within the VCN only
  ingress_security_rules {
    protocol    = "6"
    source      = "10.20.0.0/16"
    source_type = "CIDR_BLOCK"
    stateless   = false

    tcp_options {
      min = 11434
      max = 11434
    }

    description = "Private Ollama API access within POC VCN"
  }
}

resource "oci_core_subnet" "poc" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.poc.id
  display_name               = "cd-ai-bot-poc-public-subnet"
  cidr_block                 = "10.20.1.0/24"
  dns_label                  = "botpoc"
  route_table_id             = oci_core_route_table.poc.id
  security_list_ids          = [oci_core_security_list.poc.id]
  prohibit_public_ip_on_vnic = false
}

resource "oci_objectstorage_bucket" "documents" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.ns.namespace
  name           = var.bucket_name

  access_type  = "NoPublicAccess"
  storage_tier = "Standard"
  versioning   = "Disabled"

  freeform_tags = {
    Project     = "CrystalDelta-AI-Bot-POC"
    Environment = "POC"
    Purpose     = "Bot documents"
  }
}