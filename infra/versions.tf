terraform {
  required_version = ">= 1.6.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 8.29"
    }
  }
}

provider "oci" {
  config_file_profile = "DEFAULT"
  region              = var.region
}