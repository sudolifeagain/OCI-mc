output "instance_inventory" {
  description = "Ansibleの適用対象を確認するための機密扱いのインスタンス情報である。"
  sensitive   = true
  value = {
    display_name        = data.oci_core_instance.minecraft.display_name
    availability_domain = data.oci_core_instance.minecraft.availability_domain
    shape               = data.oci_core_instance.minecraft.shape
    state               = data.oci_core_instance.minecraft.state
  }
}
