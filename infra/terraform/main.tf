data "oci_core_instance" "minecraft" {
  instance_id = var.instance_ocid
}

check "instance_compartment" {
  assert {
    condition     = data.oci_core_instance.minecraft.compartment_id == var.compartment_ocid
    error_message = "指定したインスタンスは管理対象コンパートメントに属していない。"
  }
}

check "instance_protection" {
  assert {
    condition     = data.oci_core_instance.minecraft.state != "TERMINATED"
    error_message = "管理対象インスタンスは終了済みである。"
  }
}
