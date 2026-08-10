from django.test import TestCase

# Create your tests here.

# TODO: Model
# User               User
# Role               Role
# Permission         Permission
# User-Role relation RoleBinding
# Role-Permission relation Role


# TODO:
#  1. Create user, invite user (add role to user)
#  2. Create role (create role and assign a permission set)
#  3. APIView controls user access permission (get the codename for the user's API access action, get the user's role-permissions, check whether it's included)
#  4. Get permission set (get by category, scope: system, org, app)
#  5. Define permission bits (organize and categorize all permission bits, and redefine permission names in the Model)
#  7. Add built-in roles
#  6. Update user Model/Serializer/API, remove the old role field, link to the new role
#  8. Translate permission bit names (build a dict, key is codename, value is the translation)
#  9. Update the role associated with the user-org relation, update the table schema
#  10. Frontend fetches all permissions and adds the corresponding permission control directive to each button
