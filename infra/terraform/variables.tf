variable "region" {
  description = "OCIリージョン識別子である。Resource Managerのスタック変数で指定する。"
  type        = string

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+-[0-9]+$", var.region))
    error_message = "regionにはap-osaka-1形式のOCIリージョン識別子を指定する必要がある。"
  }
}

variable "compartment_ocid" {
  description = "Minecraftインスタンスを含むコンパートメントのOCIDである。"
  type        = string
  sensitive   = true

  validation {
    condition     = startswith(var.compartment_ocid, "ocid1.compartment.") || startswith(var.compartment_ocid, "ocid1.tenancy.")
    error_message = "compartment_ocidにはコンパートメントまたはルート・コンパートメントのOCIDを指定する必要がある。"
  }
}

variable "instance_ocid" {
  description = "既存MinecraftインスタンスのOCIDである。"
  type        = string
  sensitive   = true

  validation {
    condition     = startswith(var.instance_ocid, "ocid1.instance.")
    error_message = "instance_ocidにはOCI ComputeインスタンスのOCIDを指定する必要がある。"
  }
}
